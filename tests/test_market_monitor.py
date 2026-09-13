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
        self.checked_batches = []

    async def notify_purchased(self, slug, price, channel_username):
        self.purchased.append((slug, price, channel_username))

    async def notify_ignored(self, slug, price, max_price, channel_username, reason):
        self.ignored.append((slug, price, max_price, channel_username, reason))

    async def notify_failed(self, slug, price, channel_username, reason):
        self.failed.append((slug, price, channel_username, reason))

    async def notify_listing_checked(self, name, price, link):
        self.checked.append((name, price, link))

    async def notify_listings_checked_batch(self, items, interval):
        self.checked_batches.append((items, interval))


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
    assert monitor._verbose_buffer == []


async def test_verbose_immediate_mode_notifies_right_away(repo):
    """interval == 0 means the old "one message per listing, immediately"
    behavior — no batching, no waiting for a flush."""
    await repo.set_verbose_poll_log(True)
    await repo.set_verbose_poll_interval(0)
    client = FakeMarketClient()
    executor = make_executor(PurchaseOutcome(success=True, price_stars=999))
    monitor, notifier, _ = await make_market_monitor(repo, client, executor=executor)

    await monitor.handle_candidate(gift_id=1, unique=FakeUniqueGift("MarketGift-8", 999, title="Plush Pepe"))

    assert len(notifier.checked) == 1
    name, price, link = notifier.checked[0]
    assert name == "Plush Pepe"
    assert price == 999
    assert link == "https://t.me/nft/MarketGift-8"
    assert monitor._verbose_buffer == []  # nothing left to flush


async def test_verbose_aggregated_mode_buffers_instead_of_sending(repo):
    """interval > 0 (the default, 5s) must NOT send a message per listing —
    it accumulates into the buffer for verbose_flush_loop to drain."""
    await repo.set_verbose_poll_log(True)  # default interval stays 5
    client = FakeMarketClient()
    executor = make_executor(PurchaseOutcome(success=True, price_stars=100))
    monitor, notifier, _ = await make_market_monitor(repo, client, executor=executor)

    await monitor.handle_candidate(gift_id=1, unique=FakeUniqueGift("MarketGift-8b", 100, title="Plush Pepe"))

    assert notifier.checked == []  # not sent immediately
    assert len(monitor._verbose_buffer) == 1
    name, price, link = monitor._verbose_buffer[0]
    assert name == "Plush Pepe"
    assert price == 100


async def test_flush_verbose_buffer_sends_one_aggregated_message(repo):
    await repo.set_verbose_poll_log(True)
    client = FakeMarketClient()
    executor = make_executor(PurchaseOutcome(success=True, price_stars=100))
    monitor, notifier, _ = await make_market_monitor(repo, client, executor=executor)

    await monitor.handle_candidate(gift_id=1, unique=FakeUniqueGift("A", 100, title="Gift A"))
    await monitor.handle_candidate(gift_id=1, unique=FakeUniqueGift("B", 150, title="Gift B"))

    await monitor.flush_verbose_buffer(interval=5)

    assert len(notifier.checked_batches) == 1
    items, interval = notifier.checked_batches[0]
    assert interval == 5
    assert [i[0] for i in items] == ["Gift A", "Gift B"]
    assert monitor._verbose_buffer == []  # drained


async def test_flush_verbose_buffer_sends_nothing_when_empty(repo):
    await repo.set_verbose_poll_log(True)
    client = FakeMarketClient()
    monitor, notifier, _ = await make_market_monitor(repo, client)

    await monitor.flush_verbose_buffer(interval=5)

    assert notifier.checked_batches == []


async def test_verbose_poll_log_fires_even_for_over_budget_listing(repo):
    """Explicitly required: verbose mode reports a listing even when its
    price is above MAX_NFT_PRICE (it would otherwise just be ignored)."""
    await repo.set_verbose_poll_log(True)
    await repo.set_verbose_poll_interval(0)
    client = FakeMarketClient()
    executor = make_executor(PurchaseOutcome(success=True, price_stars=5000))
    monitor, notifier, _ = await make_market_monitor(repo, client, executor=executor)

    await monitor.handle_candidate(gift_id=1, unique=FakeUniqueGift("MarketGift-9", 5000))

    assert len(notifier.checked) == 1
    assert notifier.ignored  # still ignored per the price rule
    assert executor.calls == []  # and never purchased


async def test_verbose_does_not_repeat_for_a_listing_already_seen(repo):
    """A listing must be reported (verbose or otherwise) at most once in
    its lifetime — a still-listed gift re-seen on a later poll must NOT be
    reported again, whether via the immediate or the aggregated path."""
    await repo.set_verbose_poll_log(True)
    await repo.set_verbose_poll_interval(0)
    client = FakeMarketClient()
    executor = make_executor(PurchaseOutcome(success=True, price_stars=100))
    monitor, notifier, _ = await make_market_monitor(repo, client, executor=executor)

    unique = FakeUniqueGift("MarketGift-10", 100)
    await monitor.handle_candidate(gift_id=1, unique=unique)
    await monitor.handle_candidate(gift_id=1, unique=unique)

    assert len(notifier.checked) == 1  # NOT 2 — no repeat on the second sighting
    assert executor.calls == ["MarketGift-10"]  # still only purchased once


async def test_verbose_aggregated_mode_does_not_rebuffer_already_seen_listing(repo):
    await repo.set_verbose_poll_log(True)  # default interval stays 5 (buffered)
    client = FakeMarketClient()
    executor = make_executor(PurchaseOutcome(success=True, price_stars=100))
    monitor, notifier, _ = await make_market_monitor(repo, client, executor=executor)

    unique = FakeUniqueGift("MarketGift-10b", 100)
    await monitor.handle_candidate(gift_id=1, unique=unique)
    await monitor.handle_candidate(gift_id=1, unique=unique)

    assert len(monitor._verbose_buffer) == 1  # not added twice


async def test_first_scan_processes_existing_listings_like_new_ones(repo):
    """Regression/confirmation test: the very first scan_once() call (e.g.
    right after /startg on an empty database) must evaluate every
    currently-listed resale gift exactly like a later "newly appeared"
    one — there is no "ignore the first snapshot" special case anywhere in
    claim_listing/handle_candidate."""
    client = FakeMarketClient(
        star_gifts_result=FakeGetStarGiftsResult([FakeStarGiftType(1, availability_resale=3)]),
        resale_by_gift_id={
            1: [
                FakeUniqueGift("Existing-1", 100),  # within budget (<=200)
                FakeUniqueGift("Existing-2", 150),  # within budget
                FakeUniqueGift("Existing-3", 999),  # over budget
            ]
        },
    )
    executor = make_executor(PurchaseOutcome(success=True, price_stars=100))
    monitor, notifier, _ = await make_market_monitor(repo, client, executor=executor)

    await monitor.scan_once()

    # All three pre-existing listings were claimed and evaluated, not skipped.
    for slug in ("Existing-1", "Existing-2", "Existing-3"):
        assert await repo.get_listing(slug) is not None
    assert sorted(executor.calls) == ["Existing-1", "Existing-2"]  # bought, within budget
    assert notifier.ignored  # Existing-3 was evaluated and ignored, not skipped

    # The one-time confirmation log's tallies must reflect this.
    assert monitor._first_scan_logged is True
    assert monitor._cycle_new_count == 3
    assert monitor._cycle_within_budget_count == 2


async def test_first_scan_log_message_is_emitted_once(repo, caplog):
    import logging

    client = FakeMarketClient(
        star_gifts_result=FakeGetStarGiftsResult([FakeStarGiftType(1, availability_resale=1)]),
        resale_by_gift_id={1: [FakeUniqueGift("Existing-4", 50)]},
    )
    executor = make_executor(PurchaseOutcome(success=True, price_stars=50))
    monitor, notifier, _ = await make_market_monitor(repo, client, executor=executor)

    with caplog.at_level(logging.INFO, logger="market_monitor"):
        await monitor.scan_once()
        await monitor.scan_once()  # second cycle: must NOT log "Birinchi tekshiruv" again

    first_scan_logs = [r for r in caplog.records if "Birinchi tekshiruv" in r.message]
    assert len(first_scan_logs) == 1
    assert "1 ta mavjud listing topildi" in first_scan_logs[0].message
    assert "1 tasi MAX_NFT_PRICE dan past" in first_scan_logs[0].message
