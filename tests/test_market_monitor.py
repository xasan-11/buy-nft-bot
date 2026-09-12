from __future__ import annotations

from telethon.errors import FloodWaitError, RPCError
from telethon.tl import functions

from src.database.repository import ListingStatus, MARKET_MONITORING_KEY, PurchaseResult
from src.marketplace.purchase import PurchaseOutcome
from src.monitoring.channel_monitor import MonitorState
from src.monitoring.market_monitor import MarketMonitor, resalable_gift_ids


class FakeStarGiftType:
    def __init__(self, id: int, availability_resale=None):
        self.id = id
        self.availability_resale = availability_resale


class FakeAmount:
    def __init__(self, amount: int):
        self.amount = amount


class FakeUniqueGift:
    def __init__(self, slug: str, price: int, resale_ton_only: bool = False, title: str = None):
        self.slug = slug
        self.resell_amount = [FakeAmount(price)]
        self.resale_ton_only = resale_ton_only
        self.title = title


class FakeGetStarGiftsResult:
    def __init__(self, gifts):
        self.gifts = gifts


class FakeGetResaleResult:
    def __init__(self, gifts):
        self.gifts = gifts


class FakeMarketClient:
    def __init__(self, star_gifts_result=None, resale_by_gift_id=None, resale_error=None):
        self.star_gifts_result = star_gifts_result or FakeGetStarGiftsResult([])
        self.resale_by_gift_id = resale_by_gift_id or {}
        self.resale_error = resale_error
        self.resale_calls = []

    async def __call__(self, request):
        if isinstance(request, functions.payments.GetStarGiftsRequest):
            return self.star_gifts_result
        if isinstance(request, functions.payments.GetResaleStarGiftsRequest):
            self.resale_calls.append(request.gift_id)
            if self.resale_error:
                raise self.resale_error
            return FakeGetResaleResult(self.resale_by_gift_id.get(request.gift_id, []))
        raise AssertionError(f"unexpected request {request}")


class FakeNotifier:
    def __init__(self):
        self.purchased = []
        self.ignored = []
        self.failed = []
        self.checked = []

    async def notify_purchased(self, slug, price, channel_username):
        self.purchased.append((slug, price, channel_username))

    async def notify_ignored(self, slug, price, max_price, channel_username, reason):
        self.ignored.append((slug, price, max_price, channel_username, reason))

    async def notify_failed(self, slug, price, channel_username, reason):
        self.failed.append((slug, price, channel_username, reason))

    async def notify_listing_checked(self, name, price, link):
        self.checked.append((name, price, link))


def make_executor(outcome: PurchaseOutcome):
    calls = []

    async def executor(client, slug, max_price, is_monitoring_running):
        calls.append(slug)
        return outcome

    executor.calls = calls
    return executor


async def make_market_monitor(repo, client, executor=None, running=True):
    notifier = FakeNotifier()
    state = MonitorState(repo, key=MARKET_MONITORING_KEY)
    await state.load()
    if running:
        await state.start()
    monitor = MarketMonitor(
        user_client=client,
        repo=repo,
        state=state,
        notifier=notifier,
        purchase_executor=executor or make_executor(PurchaseOutcome(success=True, price_stars=100)),
        request_spacing_seconds=0,  # keep tests fast; spacing itself is covered in test_market_monitor_concurrency.py
    )
    return monitor, notifier, state


def test_resalable_gift_ids_filters_by_flag():
    gifts = [
        FakeStarGiftType(1, availability_resale=5),
        FakeStarGiftType(2, availability_resale=None),
        FakeStarGiftType(3, availability_resale=0),
    ]
    assert resalable_gift_ids(gifts) == [1]


async def test_within_budget_listing_is_purchased(repo):
    client = FakeMarketClient()
    executor = make_executor(PurchaseOutcome(success=True, price_stars=100))
    monitor, notifier, _ = await make_market_monitor(repo, client, executor=executor)

    unique = FakeUniqueGift("MarketGift-1", 100)
    await monitor.handle_candidate(gift_id=42, unique=unique)

    assert executor.calls == ["MarketGift-1"]
    assert len(notifier.purchased) == 1
    listing = await repo.get_listing("MarketGift-1")
    assert listing["status"] == ListingStatus.PURCHASED
    assert listing["source"] == "market"


async def test_over_budget_listing_is_ignored(repo):
    client = FakeMarketClient()
    executor = make_executor(PurchaseOutcome(success=True, price_stars=999))
    monitor, notifier, _ = await make_market_monitor(repo, client, executor=executor)

    unique = FakeUniqueGift("MarketGift-2", 999)
    await monitor.handle_candidate(gift_id=42, unique=unique)

    assert executor.calls == []
    assert len(notifier.ignored) == 1
    listing = await repo.get_listing("MarketGift-2")
    assert listing["status"] == ListingStatus.IGNORED_PRICE


async def test_ton_only_listing_is_skipped(repo):
    client = FakeMarketClient(resale_by_gift_id={42: [FakeUniqueGift("MarketGift-3", 50, resale_ton_only=True)]})
    executor = make_executor(PurchaseOutcome(success=True, price_stars=50))
    monitor, notifier, _ = await make_market_monitor(repo, client, executor=executor)

    await monitor.scan_gift_type(42)

    assert executor.calls == []
    assert await repo.get_listing("MarketGift-3") is None


async def test_duplicate_slug_across_scans_is_not_purchased_twice(repo):
    client = FakeMarketClient()
    executor = make_executor(PurchaseOutcome(success=True, price_stars=100))
    monitor, notifier, _ = await make_market_monitor(repo, client, executor=executor)

    unique = FakeUniqueGift("MarketGift-4", 100)
    await monitor.handle_candidate(gift_id=42, unique=unique)
    await monitor.handle_candidate(gift_id=42, unique=unique)  # seen again on next poll

    assert executor.calls == ["MarketGift-4"]


async def test_duplicate_slug_already_claimed_by_channel_monitor_is_skipped(repo):
    await repo.claim_listing(
        slug="SharedGift-1", gift_id=1, channel_id=-100, message_id=1, price_stars=50, source="channel"
    )
    client = FakeMarketClient()
    executor = make_executor(PurchaseOutcome(success=True, price_stars=50))
    monitor, notifier, _ = await make_market_monitor(repo, client, executor=executor)

    await monitor.handle_candidate(gift_id=1, unique=FakeUniqueGift("SharedGift-1", 50))

    assert executor.calls == []  # channel monitor already owns this slug


async def test_market_stopped_does_not_purchase(repo):
    client = FakeMarketClient()
    executor = make_executor(PurchaseOutcome(success=True, price_stars=100))
    monitor, notifier, state = await make_market_monitor(repo, client, executor=executor, running=False)

    await monitor.handle_candidate(gift_id=42, unique=FakeUniqueGift("MarketGift-5", 100))

    assert executor.calls == []
    assert await repo.get_listing("MarketGift-5") is None


async def test_scan_once_only_queries_resalable_gift_types(repo):
    client = FakeMarketClient(
        star_gifts_result=FakeGetStarGiftsResult(
            [FakeStarGiftType(1, availability_resale=3), FakeStarGiftType(2, availability_resale=None)]
        )
    )
    monitor, notifier, _ = await make_market_monitor(repo, client)

    await monitor.scan_once()

    assert client.resale_calls == [1]


async def test_flood_wait_during_gift_type_scan_does_not_raise(repo):
    client = FakeMarketClient(resale_error=FloodWaitError(None, capture=0))
    monitor, notifier, _ = await make_market_monitor(repo, client)

    await monitor.scan_gift_type(42)  # should swallow the FloodWait, not raise


async def test_rpc_error_during_gift_type_scan_does_not_raise(repo):
    client = FakeMarketClient(resale_error=RPCError(None, "GIFT_NOT_FOUND"))
    monitor, notifier, _ = await make_market_monitor(repo, client)

    await monitor.scan_gift_type(42)  # should log and continue, not raise


async def test_failed_purchase_is_recorded_and_notified(repo):
    client = FakeMarketClient()
    executor = make_executor(PurchaseOutcome(success=False, price_stars=80, error="STARGIFT_UNAVAILABLE"))
    monitor, notifier, _ = await make_market_monitor(repo, client, executor=executor)

    await monitor.handle_candidate(gift_id=1, unique=FakeUniqueGift("MarketGift-6", 80))

    listing = await repo.get_listing("MarketGift-6")
    assert listing["status"] == ListingStatus.FAILED
    assert notifier.failed[0][3] == "STARGIFT_UNAVAILABLE"
    stats = await repo.get_stats()
    assert stats["failed"] == 1
    assert stats["market_detected"] == 1


async def test_verbose_poll_log_off_by_default_sends_no_checked_notification(repo):
    client = FakeMarketClient()
    executor = make_executor(PurchaseOutcome(success=True, price_stars=999))
    monitor, notifier, _ = await make_market_monitor(repo, client, executor=executor)

    await monitor.handle_candidate(gift_id=1, unique=FakeUniqueGift("MarketGift-7", 999, title="Plush Pepe"))

    assert notifier.checked == []


async def test_verbose_poll_log_on_notifies_for_every_candidate(repo):
    await repo.set_verbose_poll_log(True)
    client = FakeMarketClient()
    executor = make_executor(PurchaseOutcome(success=True, price_stars=999))
    monitor, notifier, _ = await make_market_monitor(repo, client, executor=executor)

    await monitor.handle_candidate(gift_id=1, unique=FakeUniqueGift("MarketGift-8", 999, title="Plush Pepe"))

    assert len(notifier.checked) == 1
    name, price, link = notifier.checked[0]
    assert name == "Plush Pepe"
    assert price == 999
    assert link == "https://t.me/nft/MarketGift-8"


async def test_verbose_poll_log_fires_even_for_over_budget_listing(repo):
    """Explicitly required: verbose mode reports a listing even when its
    price is above MAX_NFT_PRICE (it would otherwise just be ignored)."""
    await repo.set_verbose_poll_log(True)
    client = FakeMarketClient()
    executor = make_executor(PurchaseOutcome(success=True, price_stars=5000))
    monitor, notifier, _ = await make_market_monitor(repo, client, executor=executor)

    await monitor.handle_candidate(gift_id=1, unique=FakeUniqueGift("MarketGift-9", 5000))

    assert len(notifier.checked) == 1
    assert notifier.ignored  # still ignored per the price rule
    assert executor.calls == []  # and never purchased


async def test_verbose_poll_log_fires_again_on_duplicate_scan(repo):
    """The spec asks for a message per listing found *this cycle*, so a
    still-listed gift re-seen on a later poll gets reported again even
    though claim_listing rejects it as a duplicate."""
    await repo.set_verbose_poll_log(True)
    client = FakeMarketClient()
    executor = make_executor(PurchaseOutcome(success=True, price_stars=100))
    monitor, notifier, _ = await make_market_monitor(repo, client, executor=executor)

    unique = FakeUniqueGift("MarketGift-10", 100)
    await monitor.handle_candidate(gift_id=1, unique=unique)
    await monitor.handle_candidate(gift_id=1, unique=unique)

    assert len(notifier.checked) == 2
    assert executor.calls == ["MarketGift-10"]  # still only purchased once
