from __future__ import annotations

from src.database.repository import ListingStatus, PurchaseResult
from src.marketplace.purchase import PurchaseOutcome
from src.monitoring.channel_monitor import ChannelMonitor, MonitorState

CHANNEL_ID = -1001
CHANNEL_USERNAME = "example"
MAX_PRICE = 200


class FakeNotifier:
    def __init__(self):
        self.purchased = []
        self.ignored = []
        self.failed = []
        self.stopped = 0
        self.started = 0

    async def notify_purchased(self, slug, price, channel_username):
        self.purchased.append((slug, price, channel_username))

    async def notify_ignored(self, slug, price, max_price, channel_username, reason):
        self.ignored.append((slug, price, max_price, channel_username, reason))

    async def notify_failed(self, slug, price, channel_username, reason):
        self.failed.append((slug, price, channel_username, reason))

    async def notify_monitoring_stopped(self):
        self.stopped += 1

    async def notify_monitoring_started(self):
        self.started += 1


def make_executor(outcome: PurchaseOutcome):
    calls = []

    async def executor(client, slug, max_price, is_monitoring_running):
        calls.append(slug)
        return outcome

    executor.calls = calls
    return executor


async def make_monitor(repo, executor=None, running: bool = True):
    notifier = FakeNotifier()
    state = MonitorState(repo)
    await state.load()
    if running:
        await state.start()
    monitor = ChannelMonitor(
        user_client=None,
        repo=repo,
        state=state,
        max_price=MAX_PRICE,
        notifier=notifier,
        purchase_executor=executor or make_executor(PurchaseOutcome(success=True, price_stars=100)),
    )
    monitor.channel_ids = {CHANNEL_ID}
    return monitor, notifier, state


def listing_text(slug: str, price: int | None) -> str:
    price_part = f"Price: {price} ⭐" if price is not None else ""
    return f"New gift! https://t.me/nft/{slug} {price_part}"


async def test_price_200_is_eligible_and_purchased(repo):
    executor = make_executor(PurchaseOutcome(success=True, price_stars=200))
    monitor, notifier, _ = await make_monitor(repo, executor=executor)

    await monitor.handle_message(CHANNEL_ID, CHANNEL_USERNAME, 1, listing_text("Gift-1", 200))

    assert executor.calls == ["Gift-1"]
    assert len(notifier.purchased) == 1
    listing = await repo.get_listing("Gift-1")
    assert listing["status"] == ListingStatus.PURCHASED


async def test_price_201_is_ignored_no_purchase(repo):
    executor = make_executor(PurchaseOutcome(success=True, price_stars=201))
    monitor, notifier, _ = await make_monitor(repo, executor=executor)

    await monitor.handle_message(CHANNEL_ID, CHANNEL_USERNAME, 1, listing_text("Gift-2", 201))

    assert executor.calls == []
    assert len(notifier.ignored) == 1
    listing = await repo.get_listing("Gift-2")
    assert listing["status"] == ListingStatus.IGNORED_PRICE


async def test_price_125_is_eligible_and_purchased(repo):
    executor = make_executor(PurchaseOutcome(success=True, price_stars=125))
    monitor, notifier, _ = await make_monitor(repo, executor=executor)

    await monitor.handle_message(CHANNEL_ID, CHANNEL_USERNAME, 1, listing_text("Gift-3", 125))

    assert executor.calls == ["Gift-3"]
    assert len(notifier.purchased) == 1


async def test_missing_price_is_ignored(repo):
    executor = make_executor(PurchaseOutcome(success=True, price_stars=100))
    monitor, notifier, _ = await make_monitor(repo, executor=executor)

    await monitor.handle_message(CHANNEL_ID, CHANNEL_USERNAME, 1, listing_text("Gift-4", None))

    assert executor.calls == []
    listing = await repo.get_listing("Gift-4")
    assert listing["status"] == ListingStatus.IGNORED_UNKNOWN_PRICE


async def test_duplicate_listing_is_not_purchased_twice(repo):
    executor = make_executor(PurchaseOutcome(success=True, price_stars=100))
    monitor, notifier, _ = await make_monitor(repo, executor=executor)

    text = listing_text("Gift-5", 100)
    await monitor.handle_message(CHANNEL_ID, CHANNEL_USERNAME, 1, text)
    await monitor.handle_message(CHANNEL_ID, CHANNEL_USERNAME, 2, text)  # edited/reposted

    assert executor.calls == ["Gift-5"]
    assert len(notifier.purchased) == 1


async def test_stop_disables_purchasing(repo):
    executor = make_executor(PurchaseOutcome(success=True, price_stars=100))
    monitor, notifier, state = await make_monitor(repo, executor=executor, running=True)

    await state.stop()
    await monitor.handle_message(CHANNEL_ID, CHANNEL_USERNAME, 1, listing_text("Gift-6", 100))

    assert executor.calls == []
    assert await repo.get_listing("Gift-6") is None  # never even claimed


async def test_sstart_enables_purchasing(repo):
    executor = make_executor(PurchaseOutcome(success=True, price_stars=100))
    monitor, notifier, state = await make_monitor(repo, executor=executor, running=False)

    await state.start()
    await monitor.handle_message(CHANNEL_ID, CHANNEL_USERNAME, 1, listing_text("Gift-7", 100))

    assert executor.calls == ["Gift-7"]


async def test_purchase_while_stopped_must_not_happen(repo):
    executor = make_executor(PurchaseOutcome(success=True, price_stars=100))
    monitor, notifier, state = await make_monitor(repo, executor=executor, running=True)

    # Simulate /stop landing after eligibility passes but before the purchase
    # stage: handle_message re-checks state.is_running() right before it
    # would move to PURCHASE_ATTEMPTED.
    original_is_running = state.is_running
    call_count = {"n": 0}

    def flaky_is_running():
        call_count["n"] += 1
        # First check (top of handle_message) still running, second check
        # (immediately before purchase) reports stopped.
        return call_count["n"] == 1

    state.is_running = flaky_is_running

    await monitor.handle_message(CHANNEL_ID, CHANNEL_USERNAME, 1, listing_text("Gift-8", 100))

    assert executor.calls == []
    listing = await repo.get_listing("Gift-8")
    assert listing["status"] == ListingStatus.IGNORED_STOPPED


async def test_successful_purchase_records_result(repo):
    executor = make_executor(PurchaseOutcome(success=True, price_stars=150))
    monitor, notifier, _ = await make_monitor(repo, executor=executor)

    await monitor.handle_message(CHANNEL_ID, CHANNEL_USERNAME, 1, listing_text("Gift-9", 150))

    stats = await repo.get_stats()
    assert stats["success"] == 1
    assert stats["failed"] == 0
    assert notifier.purchased[0] == ("Gift-9", 150, CHANNEL_USERNAME)


async def test_failed_purchase_records_result_and_notifies(repo):
    executor = make_executor(PurchaseOutcome(success=False, price_stars=150, error="BALANCE_TOO_LOW"))
    monitor, notifier, _ = await make_monitor(repo, executor=executor)

    await monitor.handle_message(CHANNEL_ID, CHANNEL_USERNAME, 1, listing_text("Gift-10", 150))

    stats = await repo.get_stats()
    assert stats["failed"] == 1
    assert stats["success"] == 0
    listing = await repo.get_listing("Gift-10")
    assert listing["status"] == ListingStatus.FAILED
    assert notifier.failed[0][3] == "BALANCE_TOO_LOW"


async def test_unidentifiable_post_is_never_claimed(repo):
    executor = make_executor(PurchaseOutcome(success=True, price_stars=100))
    monitor, notifier, _ = await make_monitor(repo, executor=executor)

    await monitor.handle_message(CHANNEL_ID, CHANNEL_USERNAME, 1, "Just a regular announcement, nothing for sale.")

    assert executor.calls == []
    stats = await repo.get_stats()
    assert stats["total_detected"] == 0
