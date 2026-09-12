from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Callable, Optional

from telethon.errors import FloodWaitError, RPCError
from telethon.tl import functions

from ..database.repository import ListingStatus, PurchaseResult, Repository
from ..marketplace.purchase import execute_purchase

logger = logging.getLogger("market_monitor")

# Regardless of POLL_INTERVAL, never *target* a faster cadence than this
# between the start of one full pass and the next — a full pass calls
# payments.getResaleStarGifts once per resalable gift type (now in
# parallel), so a tight loop here would still multiply into many requests
# in a short window and risk FloodWait.
MIN_SCAN_INTERVAL_SECONDS = 5
IDLE_SLEEP_SECONDS = 5
# Absolute floor between the end of one cycle and the start of the next,
# even if the cycle itself took longer than MIN_SCAN_INTERVAL_SECONDS — a
# cycle finishing fast should not immediately fire another burst of
# requests back-to-back.
MIN_INTER_CYCLE_SLEEP_SECONDS = 2
# How many times a single gift type's request retries after a FloodWait
# before that gift type is skipped for this cycle (it will be retried on
# the next poll).
MAX_FLOOD_WAIT_RETRIES = 3
# Minimum time between the START of consecutive getResaleStarGifts calls,
# enforced globally across ALL concurrent tasks — not just a concurrency
# cap. This matters because a semaphore only limits how many requests are
# in flight *at once*; it does nothing to stop several tasks whose FloodWait
# sleep happens to end around the same moment from all retrying together,
# which in practice re-triggered a *larger* FloodWait each round (20s, then
# 43s, ...). Spacing out request starts like this is what actually breaks
# that loop. A small random jitter is added so retries don't resynchronize.
MIN_REQUEST_SPACING_SECONDS = 1.5


def resalable_gift_ids(gifts) -> list:
    """Pure helper (easy to unit test): which gift *types* currently have at
    least one instance listed for resale, per StarGift.availability_resale."""
    return [g.id for g in gifts if getattr(g, "availability_resale", None)]


class MarketMonitor:
    """Independent, polling-based counterpart to ChannelMonitor.

    Instead of reacting to channel posts, this periodically asks Telegram
    itself which collectible gifts are currently listed for resale
    (payments.getStarGifts to enumerate gift types, then
    payments.getResaleStarGifts per resalable type) and runs the same kind
    of eligibility/purchase pipeline against each listing it finds. It
    shares processed_listings with channel monitoring (via claim_listing),
    so the same gift is never bought twice regardless of which pipeline
    spotted it first, and it is controlled by its own persisted
    MonitorState (/startg, /stopg) — entirely independent of channel
    monitoring's /sstart and /stop.
    """

    def __init__(
        self,
        user_client,
        repo: Repository,
        state,
        notifier,
        poll_interval: int = 5,
        listings_per_gift: int = 30,
        purchase_executor: Callable = execute_purchase,
        request_spacing_seconds: float = MIN_REQUEST_SPACING_SECONDS,
    ):
        self.user_client = user_client
        self.repo = repo
        self.state = state
        self.notifier = notifier
        self.scan_interval = max(poll_interval, MIN_SCAN_INTERVAL_SECONDS)
        self.listings_per_gift = listings_per_gift
        self.purchase_executor = purchase_executor
        # Overridable (e.g. in tests) so the real production spacing doesn't
        # force every test that scans multiple gift types to actually sleep
        # for real seconds.
        self.request_spacing_seconds = request_spacing_seconds
        self._stop_forever = asyncio.Event()
        self._request_gate_lock = asyncio.Lock()
        self._next_request_at = 0.0

    async def _throttle(self) -> None:
        """Blocks until it is this caller's turn to start a Telegram
        request, enforcing request_spacing_seconds between the start of any
        two getStarGifts/getResaleStarGifts calls made by this monitor,
        regardless of how many are concurrently in flight."""
        async with self._request_gate_lock:
            now = time.monotonic()
            wait = self._next_request_at - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()
            jitter = random.uniform(0, self.request_spacing_seconds * 0.2)
            self._next_request_at = now + self.request_spacing_seconds + jitter

    def stop_forever(self) -> None:
        """Ends run_forever's loop entirely (app shutdown) — distinct from
        state.stop(), which only pauses purchasing while the loop keeps
        idling so it can pick back up on /startg."""
        self._stop_forever.set()

    async def run_forever(self) -> None:
        while not self._stop_forever.is_set():
            if not self.state.is_running():
                await asyncio.sleep(IDLE_SLEEP_SECONDS)
                continue
            try:
                elapsed = await self.scan_once()
            except Exception:
                logger.exception("Market scan iteration failed")
                elapsed = 0.0
            # Target a total cadence of self.scan_interval between cycle
            # starts, but never less than MIN_INTER_CYCLE_SLEEP_SECONDS —
            # a cycle that came back fast still gets a short breather.
            sleep_for = max(self.scan_interval - elapsed, MIN_INTER_CYCLE_SLEEP_SECONDS)
            await asyncio.sleep(sleep_for)

    async def scan_once(self) -> float:
        """Runs one full pass over every resalable gift type, in parallel
        (bounded by market_poll_concurrency), and returns how long it took
        so run_forever can pace the next cycle accordingly."""
        start = time.monotonic()
        result = None
        for attempt in range(1, MAX_FLOOD_WAIT_RETRIES + 1):
            await self._throttle()
            try:
                result = await self.user_client(functions.payments.GetStarGiftsRequest(hash=0))
                break
            except FloodWaitError as e:
                logger.warning(
                    "FloodWait while listing gift types: waiting %ss (attempt %d/%d)",
                    e.seconds, attempt, MAX_FLOOD_WAIT_RETRIES,
                )
                await asyncio.sleep(e.seconds)
            except RPCError:
                logger.exception("Failed to list gift types")
                return time.monotonic() - start
        else:
            logger.warning("Giving up on listing gift types after %d FloodWait retries", MAX_FLOOD_WAIT_RETRIES)
            return time.monotonic() - start

        gift_ids = resalable_gift_ids(getattr(result, "gifts", None) or [])
        if not gift_ids:
            elapsed = time.monotonic() - start
            logger.info("Market poll cycle completed in %.2fs, 0 gift types checked", elapsed)
            return elapsed

        concurrency = max(1, await self.repo.get_market_poll_concurrency())
        semaphore = asyncio.Semaphore(concurrency)

        async def _bounded_scan(gift_id: int) -> None:
            async with semaphore:
                if not self.state.is_running():
                    return
                await self.scan_gift_type(gift_id)

        await asyncio.gather(*(_bounded_scan(gid) for gid in gift_ids))

        elapsed = time.monotonic() - start
        logger.info(
            "Market poll cycle completed in %.2fs, %d gift types checked (concurrency=%d)",
            elapsed, len(gift_ids), concurrency,
        )
        return elapsed

    async def scan_gift_type(self, gift_id: int) -> None:
        """Fetches current resale listings for one gift type. A FloodWait on
        this specific request is retried (waiting exactly what Telegram
        asked) up to MAX_FLOOD_WAIT_RETRIES times — it never cancels or
        otherwise affects the other gift types being scanned concurrently,
        since each runs as its own task."""
        result = None
        for attempt in range(1, MAX_FLOOD_WAIT_RETRIES + 1):
            await self._throttle()
            try:
                result = await self.user_client(
                    functions.payments.GetResaleStarGiftsRequest(
                        gift_id=gift_id,
                        offset="",
                        limit=self.listings_per_gift,
                        sort_by_price=True,
                    )
                )
                break
            except FloodWaitError as e:
                logger.warning(
                    "FloodWait while scanning gift_id=%s: waiting %ss (attempt %d/%d)",
                    gift_id, e.seconds, attempt, MAX_FLOOD_WAIT_RETRIES,
                )
                await asyncio.sleep(e.seconds)
            except RPCError:
                logger.exception("Failed to fetch resale listings for gift_id=%s", gift_id)
                return
        else:
            logger.warning(
                "Giving up on gift_id=%s after %d FloodWait retries this cycle",
                gift_id, MAX_FLOOD_WAIT_RETRIES,
            )
            return

        for unique in getattr(result, "gifts", None) or []:
            slug = getattr(unique, "slug", None)
            if not slug:
                continue
            if getattr(unique, "resale_ton_only", False):
                continue  # only Stars purchases are in scope; skip TON-only listings
            await self.handle_candidate(gift_id, unique)

    async def handle_candidate(self, gift_id: int, unique) -> None:
        if not self.state.is_running():
            return

        estimate = self._estimate_price(unique)

        # /startpo verbose mode: report every listing this cycle finds,
        # regardless of whether it's new, already processed, eligible, or
        # over budget — independent of (and in addition to) the normal
        # purchased/ignored/failed notifications below.
        if await self.repo.get_verbose_poll_log():
            await self.notifier.notify_listing_checked(
                self._display_name(unique), estimate, self._listing_link(unique, gift_id)
            )

        claimed = await self.repo.claim_listing(
            slug=unique.slug,
            gift_id=gift_id,
            channel_id=None,
            message_id=None,
            price_stars=estimate,
            source="market",
        )
        if not claimed:
            return  # already seen — via an earlier scan or a channel post

        max_price = await self.repo.get_max_price()

        if estimate is not None and estimate > max_price:
            await self.repo.update_listing_status(unique.slug, ListingStatus.IGNORED_PRICE)
            await self.notifier.notify_ignored(
                unique.slug, estimate, max_price, "market", "price exceeds maximum"
            )
            return

        await self.repo.update_listing_status(unique.slug, ListingStatus.ELIGIBLE)

        if not self.state.is_running():
            await self.repo.update_listing_status(unique.slug, ListingStatus.IGNORED_STOPPED)
            return

        await self.repo.update_listing_status(unique.slug, ListingStatus.PURCHASE_ATTEMPTED)

        outcome = await self.purchase_executor(
            self.user_client, unique.slug, max_price, self.state.is_running
        )

        if outcome.success:
            await self.repo.update_listing_status(
                unique.slug, ListingStatus.PURCHASED, price_stars=outcome.price_stars
            )
            await self.repo.record_purchase_attempt(
                unique.slug, outcome.price_stars, None, PurchaseResult.SUCCESS, None
            )
            await self.notifier.notify_purchased(unique.slug, outcome.price_stars, "market")
        else:
            await self.repo.update_listing_status(unique.slug, ListingStatus.FAILED)
            await self.repo.record_purchase_attempt(
                unique.slug,
                outcome.price_stars or estimate,
                None,
                PurchaseResult.FAILED,
                outcome.error,
            )
            await self.notifier.notify_failed(
                unique.slug, outcome.price_stars or estimate, "market", outcome.error
            )

    @staticmethod
    def _display_name(unique) -> str:
        title = getattr(unique, "title", None)
        if title:
            return title
        num = getattr(unique, "num", None)
        return f"gift #{num}" if num is not None else "unknown gift"

    @staticmethod
    def _listing_link(unique, gift_id: int) -> str:
        slug = getattr(unique, "slug", None)
        if slug:
            return f"https://t.me/nft/{slug}"
        return f"gift_id={gift_id}"

    @staticmethod
    def _estimate_price(unique) -> Optional[int]:
        """Cheap pre-filter price from the resale listing itself. The
        authoritative price used for the actual eligibility gate right
        before paying still comes from payments.getPaymentForm inside
        execute_purchase, exactly as for channel-detected listings — this
        estimate only avoids starting a doomed purchase attempt for
        obviously overpriced listings."""
        amounts = getattr(unique, "resell_amount", None) or []
        if not amounts:
            return None
        return min(a.amount for a in amounts)
