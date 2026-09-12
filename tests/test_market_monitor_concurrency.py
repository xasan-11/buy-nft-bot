from __future__ import annotations

import asyncio
import time

from telethon.errors import FloodWaitError
from telethon.tl import functions

from src.database.repository import MARKET_MONITORING_KEY
from src.marketplace.purchase import PurchaseOutcome
from src.monitoring.channel_monitor import MonitorState
from src.monitoring.market_monitor import MAX_FLOOD_WAIT_RETRIES, MarketMonitor


class FakeStarGiftType:
    def __init__(self, id: int, availability_resale=1):
        self.id = id
        self.availability_resale = availability_resale


class FakeGetStarGiftsResult:
    def __init__(self, gifts):
        self.gifts = gifts


class FakeGetResaleResult:
    def __init__(self, gifts):
        self.gifts = gifts


class ConcurrencyTrackingClient:
    """Records how many GetResaleStarGiftsRequest calls were in flight at
    once, to verify the semaphore actually bounds concurrency."""

    def __init__(self, gift_ids, delay: float = 0.05):
        self.star_gifts_result = FakeGetStarGiftsResult([FakeStarGiftType(i) for i in gift_ids])
        self.delay = delay
        self.current_in_flight = 0
        self.max_in_flight = 0
        self.total_calls = 0
        self.start_times = []

    async def __call__(self, request):
        if isinstance(request, functions.payments.GetStarGiftsRequest):
            return self.star_gifts_result
        if isinstance(request, functions.payments.GetResaleStarGiftsRequest):
            self.total_calls += 1
            self.start_times.append(time.monotonic())
            self.current_in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.current_in_flight)
            await asyncio.sleep(self.delay)
            self.current_in_flight -= 1
            return FakeGetResaleResult([])
        raise AssertionError(f"unexpected request {request}")


class FloodThenSucceedClient:
    """Fails a GetResaleStarGiftsRequest with FloodWait a fixed number of
    times, then succeeds — used to verify the isolated per-request retry."""

    def __init__(self, fail_times: int):
        self.star_gifts_result = FakeGetStarGiftsResult([FakeStarGiftType(1)])
        self.fail_times = fail_times
        self.calls = 0

    async def __call__(self, request):
        if isinstance(request, functions.payments.GetStarGiftsRequest):
            return self.star_gifts_result
        if isinstance(request, functions.payments.GetResaleStarGiftsRequest):
            self.calls += 1
            if self.calls <= self.fail_times:
                raise FloodWaitError(None, capture=0)
            return FakeGetResaleResult([])
        raise AssertionError(f"unexpected request {request}")


class AlwaysFloodClient:
    def __init__(self):
        self.star_gifts_result = FakeGetStarGiftsResult([FakeStarGiftType(1)])
        self.calls = 0

    async def __call__(self, request):
        if isinstance(request, functions.payments.GetStarGiftsRequest):
            return self.star_gifts_result
        if isinstance(request, functions.payments.GetResaleStarGiftsRequest):
            self.calls += 1
            raise FloodWaitError(None, capture=0)
        raise AssertionError(f"unexpected request {request}")


class FakeNotifier:
    async def notify_purchased(self, *a, **k):
        pass

    async def notify_ignored(self, *a, **k):
        pass

    async def notify_failed(self, *a, **k):
        pass


async def make_monitor(repo, client, request_spacing_seconds: float = 0):
    state = MonitorState(repo, key=MARKET_MONITORING_KEY)
    await state.load()
    await state.start()
    return MarketMonitor(
        user_client=client,
        repo=repo,
        state=state,
        notifier=FakeNotifier(),
        purchase_executor=lambda *a, **k: PurchaseOutcome(success=True, price_stars=1),
        request_spacing_seconds=request_spacing_seconds,
    )


async def test_scan_once_respects_configured_concurrency_limit(repo):
    await repo.set_market_poll_concurrency(3)
    client = ConcurrencyTrackingClient(gift_ids=list(range(20)))
    monitor = await make_monitor(repo, client)

    await monitor.scan_once()

    assert client.total_calls == 20
    assert client.max_in_flight <= 3


async def test_scan_once_uses_higher_concurrency_when_configured(repo):
    await repo.set_market_poll_concurrency(10)
    client = ConcurrencyTrackingClient(gift_ids=list(range(20)))
    monitor = await make_monitor(repo, client)

    await monitor.scan_once()

    # With only a 0.05s delay and 20 gift types at concurrency 10, more than
    # 3 requests should have overlapped — otherwise it silently fell back to
    # sequential behavior. request_spacing_seconds=0 here (the default from
    # make_monitor) isolates this from the separate request-spacing gate,
    # which is tested on its own below.
    assert client.max_in_flight > 3


async def test_request_spacing_gate_prevents_bursts_regardless_of_concurrency(repo):
    """Regression test for the real incident this gate was added to fix:
    with a high concurrency limit, requests must still not fire in a burst
    — each request's start must be spaced out, since a burst of parallel
    getResaleStarGifts calls is what triggered an escalating FloodWait
    (20s, then 43s, ...) in production."""
    await repo.set_market_poll_concurrency(10)
    spacing = 0.1
    client = ConcurrencyTrackingClient(gift_ids=list(range(5)), delay=0.01)
    monitor = await make_monitor(repo, client, request_spacing_seconds=spacing)

    await monitor.scan_once()

    assert client.total_calls == 5
    starts = sorted(client.start_times)
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert all(gap >= spacing * 0.9 for gap in gaps), gaps
    # Concurrency alone (10) would have let all 5 start together; the gate
    # must have prevented that.
    assert client.max_in_flight <= 2


async def test_scan_once_returns_elapsed_seconds(repo):
    client = ConcurrencyTrackingClient(gift_ids=[1, 2, 3], delay=0.01)
    monitor = await make_monitor(repo, client)

    elapsed = await monitor.scan_once()

    assert isinstance(elapsed, float)
    assert elapsed >= 0


async def test_scan_gift_type_retries_after_flood_wait_then_succeeds(repo):
    client = FloodThenSucceedClient(fail_times=2)
    monitor = await make_monitor(repo, client)

    await monitor.scan_gift_type(1)  # should not raise

    assert client.calls == 3  # 2 failures + 1 success


async def test_scan_gift_type_gives_up_after_max_retries(repo):
    client = AlwaysFloodClient()
    monitor = await make_monitor(repo, client)

    await monitor.scan_gift_type(1)  # should not raise, just give up

    assert client.calls == MAX_FLOOD_WAIT_RETRIES


async def test_one_gift_types_flood_wait_does_not_block_others(repo):
    """The FloodWait-prone gift type must not prevent other gift types in
    the same cycle from being scanned."""

    class MixedClient:
        def __init__(self):
            self.star_gifts_result = FakeGetStarGiftsResult(
                [FakeStarGiftType(1), FakeStarGiftType(2), FakeStarGiftType(3)]
            )
            self.calls_by_gift = {}

        async def __call__(self, request):
            if isinstance(request, functions.payments.GetStarGiftsRequest):
                return self.star_gifts_result
            if isinstance(request, functions.payments.GetResaleStarGiftsRequest):
                gid = request.gift_id
                self.calls_by_gift[gid] = self.calls_by_gift.get(gid, 0) + 1
                if gid == 2:
                    raise FloodWaitError(None, capture=0)
                return FakeGetResaleResult([])
            raise AssertionError

    client = MixedClient()
    monitor = await make_monitor(repo, client)

    await monitor.scan_once()

    assert client.calls_by_gift[1] == 1
    assert client.calls_by_gift[3] == 1
    assert client.calls_by_gift[2] == MAX_FLOOD_WAIT_RETRIES
