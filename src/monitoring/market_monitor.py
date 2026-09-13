from __future__ import annotations

import asyncio
import datetime as dt
import logging
import random
import time
from typing import Callable, Optional

from telethon import utils
from telethon.errors import FloodWaitError, RPCError
from telethon.tl import functions

from ..database.repository import ListingStatus, PurchaseResult, Repository
from ..marketplace.balance import get_stars_balance
from ..marketplace.offer import send_offer
from ..marketplace.offer_folder import OFFER_FOLDER_NAME, add_peer_to_folder, find_offer_folder
from ..marketplace.purchase import execute_purchase
from ..marketplace.seller_profile import get_seller_profile

logger = logging.getLogger("market_monitor")

# How often the balance-watch loop polls payments.getStarsStatus while the
# auto-offer pipeline is paused for low balance — see balance_watch_loop().
BALANCE_CHECK_INTERVAL_SECONDS = 25

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


def _is_balance_too_low(error: Optional[str]) -> bool:
    return bool(error) and "BALANCE_TOO_LOW" in error.upper()


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
        offer_state=None,
        offer_executor: Callable = send_offer,
        seller_profile_fetcher: Callable = get_seller_profile,
        balance_fetcher: Callable = get_stars_balance,
        offer_folder_finder: Callable = find_offer_folder,
        offer_folder_adder: Callable = add_peer_to_folder,
    ):
        self.user_client = user_client
        self.repo = repo
        self.state = state
        self.notifier = notifier
        self.scan_interval = max(poll_interval, MIN_SCAN_INTERVAL_SECONDS)
        self.listings_per_gift = listings_per_gift
        self.purchase_executor = purchase_executor
        # Independent auto-offer pipeline (▶️ Start offer / ⏹ Stop offer) —
        # entirely separate state/dedup/action from the MAX_NFT_PRICE direct
        # purchase above. offer_state is None until main.py wires it up;
        # _maybe_send_offer no-ops in that case (e.g. in older tests).
        self.offer_state = offer_state
        self.offer_executor = offer_executor
        self.seller_profile_fetcher = seller_profile_fetcher
        self.balance_fetcher = balance_fetcher
        self.offer_folder_finder = offer_folder_finder
        self.offer_folder_adder = offer_folder_adder
        # Warn about a missing "Offer" folder at most once per process
        # lifetime, not once per successful offer — see _add_seller_to_offer_folder.
        self._offer_folder_missing_warned = False
        # Overridable (e.g. in tests) so the real production spacing doesn't
        # force every test that scans multiple gift types to actually sleep
        # for real seconds.
        self.request_spacing_seconds = request_spacing_seconds
        self._stop_forever = asyncio.Event()
        self._request_gate_lock = asyncio.Lock()
        self._next_request_at = 0.0
        # Verbose /startpo <N> aggregation buffer: entries added by
        # handle_candidate (only for genuinely NEW listings — see there),
        # drained by verbose_flush_loop every N seconds.
        self._verbose_buffer: list = []
        self._verbose_lock = asyncio.Lock()
        # Per-cycle tallies for the one-time "Birinchi tekshiruv" log line
        # confirming the first scan already evaluates whatever is currently
        # listed, not just gifts that appear after monitoring starts.
        self._first_scan_logged = False
        self._cycle_new_count = 0
        self._cycle_within_budget_count = 0

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
            self._log_first_scan_if_needed()
            return elapsed

        concurrency = max(1, await self.repo.get_market_poll_concurrency())
        semaphore = asyncio.Semaphore(concurrency)

        self._cycle_new_count = 0
        self._cycle_within_budget_count = 0

        async def _bounded_scan(gift_id: int) -> None:
            async with semaphore:
                if not self.state.is_running():
                    return
                await self.scan_gift_type(gift_id)

        await asyncio.gather(*(_bounded_scan(gid) for gid in gift_ids))
        self._log_first_scan_if_needed()

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

        # Atomic dedup/lock: only the caller that wins this insert has ever
        # "seen" this exact listing, for any purpose — verbose reporting,
        # purchase, or ignore. A listing that's still on resale next cycle
        # loses this race every time and is never reported on again.
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

        self._cycle_new_count += 1

        # Independent offer pipeline: never affects, and is never affected
        # by, the direct-purchase decision below — it reads the same
        # `unique` object but writes to its own table/state.
        await self._maybe_send_offer(gift_id, unique)

        # /startpo verbose mode: report this listing exactly once, either
        # immediately (interval == 0) or batched into the next aggregated
        # flush (interval > 0) — never both, and never again after this.
        if await self.repo.get_verbose_poll_log():
            await self._record_verbose_sighting(gift_id, unique, estimate)

        max_price = await self.repo.get_max_price()

        if estimate is not None and estimate <= max_price:
            self._cycle_within_budget_count += 1

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

    async def _maybe_send_offer(self, gift_id: int, unique) -> None:
        """Evaluates one freshly-claimed listing for the auto-offer pipeline.

        Runs only while an admin has an active "🎯 Offer boshlash" run, and
        only for gift *types* chosen in that run's gift-type picker — every
        other gift type is ignored regardless of how well its listings would
        otherwise match. Independent of MAX_NFT_PRICE: a listing can be too
        expensive to buy outright and still get an offer, or vice versa. A
        listing is only ever evaluated once, at the moment it is first
        claimed by claim_listing (same lifecycle as the purchase path) —
        this is not re-checked on later polls.
        """
        if self.offer_state is None or not self.offer_state.is_active():
            return

        # Paused for low balance (see notify_offer_paused/balance_watch_loop)
        # — do absolutely nothing until the balance-watch loop resumes this,
        # so a queue of otherwise-matching listings never produces a wall of
        # repeated BALANCE_TOO_LOW attempts/notifications.
        if self.offer_state.is_paused():
            return

        selected_gift_types = await self.repo.get_offer_selected_gift_types()
        if gift_id not in selected_gift_types:
            return

        offer_min_stars = getattr(unique, "offer_min_stars", None)
        if offer_min_stars is None:
            return  # this owner hasn't enabled offers on this gift at all

        offer_price = await self.repo.get_offer_price()
        if offer_price < offer_min_stars:
            return  # would fail with RESELL_STARS_TOO_FEW

        owner_peer = getattr(unique, "owner_id", None)
        if owner_peer is None:
            return

        profile = await self.seller_profile_fetcher(self.user_client, owner_peer)
        if profile is None:
            return  # couldn't verify the seller — fail closed, no offer

        required_level = await self.repo.get_offer_level()
        max_nft_count = await self.repo.get_offer_nft_count()
        if profile.level != required_level or profile.gift_count >= max_nft_count:
            return

        # Re-check right before reserving Stars — a /Stop-offer, or another
        # concurrently-processed listing hitting BALANCE_TOO_LOW, that
        # landed while the seller-profile lookup above was in flight must
        # still prevent this specific send, exactly like the purchase
        # pipeline's pre-payment re-check.
        if not self.offer_state.is_active() or self.offer_state.is_paused():
            return

        duration_hours = await self.repo.get_offer_expiry_hours()
        duration_seconds = duration_hours * 3600
        expires_at = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=duration_seconds)).isoformat()

        outcome = await self.offer_executor(self.user_client, owner_peer, unique.slug, offer_price, duration_seconds)

        try:
            owner_peer_id = utils.get_peer_id(owner_peer)
        except (TypeError, ValueError):
            owner_peer_id = None

        claimed = await self.repo.claim_offer_slot(
            offer_id=outcome.offer_id or 0,
            slug=unique.slug,
            gift_id=gift_id,
            price_stars=offer_price,
            duration_seconds=duration_seconds,
            expires_at=expires_at,
            owner_peer_id=owner_peer_id,
        )
        if not claimed:
            return  # already offered on this slug (shouldn't happen given claim_listing, but stay safe)

        if outcome.success:
            await self.offer_state.record_sent()
            await self.notifier.notify_offer_sent(
                self._display_name(unique),
                offer_price,
                duration_hours,
                self._listing_link(unique, gift_id),
                profile.profile_link,
            )
            await self._add_seller_to_offer_folder(owner_peer)
        else:
            await self.repo.mark_offer_failed(unique.slug, outcome.error or "unknown error")
            # Prefer the structured flag set at the source (send_offer reads
            # the RPCError's raw .message there); fall back to scanning the
            # formatted error text for any executor that doesn't set it.
            if getattr(outcome, "balance_too_low", False) or _is_balance_too_low(outcome.error):
                # Exactly one "paused" broadcast no matter how many listings
                # hit this at once — pause() only returns True for whichever
                # caller actually flips the flag (see its docstring). Every
                # other matching listing this cycle (and every future one,
                # until the balance recovers) is silently skipped by the
                # is_paused() guards above — no "Offer tashlanmadi" spam.
                if await self.offer_state.pause():
                    await self.notifier.notify_offer_paused()
            else:
                await self.notifier.notify_offer_failed(self._display_name(unique), offer_price, outcome.error)

    async def _add_seller_to_offer_folder(self, owner_peer) -> None:
        """Adds the listing owner to the "Offer" Chat Folder right after a
        successful send, via messages.getDialogFilters + messages.updateDialogFilter
        (see marketplace/offer_folder.py). Never lets a folder problem
        affect the offer pipeline itself — every failure is caught and only
        logged, and a missing folder is reported to admins at most once per
        process lifetime rather than after every single offer.
        """
        try:
            folder = await self.offer_folder_finder(self.user_client, OFFER_FOLDER_NAME)
        except Exception:
            logger.exception("Failed to look up the '%s' chat folder", OFFER_FOLDER_NAME)
            return

        if folder is None:
            if not self._offer_folder_missing_warned:
                self._offer_folder_missing_warned = True
                await self.notifier.notify_offer_folder_missing(OFFER_FOLDER_NAME)
            return

        try:
            await self.offer_folder_adder(self.user_client, folder, owner_peer)
        except Exception:
            logger.exception("Failed to add seller to the '%s' chat folder", OFFER_FOLDER_NAME)

    async def _record_verbose_sighting(self, gift_id: int, unique, estimate: Optional[int]) -> None:
        interval = await self.repo.get_verbose_poll_interval()
        entry = (self._display_name(unique), estimate, self._listing_link(unique, gift_id))
        if interval <= 0:
            await self.notifier.notify_listing_checked(*entry)
        else:
            async with self._verbose_lock:
                self._verbose_buffer.append(entry)

    async def verbose_flush_loop(self) -> None:
        """Independent timer that drains the verbose buffer into one
        aggregated message every verbose_poll_interval seconds — decoupled
        from the actual scan cadence (MARKET_POLL_INTERVAL_SECONDS), which
        only controls how often Telegram is polled for listings, not how
        often admins are told about what was found."""
        while not self._stop_forever.is_set():
            verbose_on = await self.repo.get_verbose_poll_log()
            interval = await self.repo.get_verbose_poll_interval()
            if not verbose_on or interval <= 0:
                # Immediate (interval == 0) mode needs no flushing, and if
                # verbose is off nothing should be accumulating anyway —
                # just re-check periodically in case an admin flips it on.
                await asyncio.sleep(1)
                continue
            await asyncio.sleep(interval)
            await self.flush_verbose_buffer(interval)

    async def flush_verbose_buffer(self, interval: int) -> None:
        async with self._verbose_lock:
            items = self._verbose_buffer
            self._verbose_buffer = []
        if items:
            await self.notifier.notify_listings_checked_batch(items, interval)

    async def balance_watch_loop(self) -> None:
        """Polls payments.getStarsStatus every BALANCE_CHECK_INTERVAL_SECONDS
        and auto-resumes the offer pipeline once the balance is back at or
        above the configured offer price — see check_balance_once() for the
        actual per-tick logic (split out so it's directly unit-testable
        without waiting on a real sleep loop)."""
        while not self._stop_forever.is_set():
            await asyncio.sleep(BALANCE_CHECK_INTERVAL_SECONDS)
            await self.check_balance_once()

    async def check_balance_once(self) -> None:
        """One tick of the balance watch: a no-op unless the offer run is
        both active and currently paused, so this never spends an API call
        needlessly (e.g. while no offer run is happening at all)."""
        if self.offer_state is None:
            return
        if not self.offer_state.is_active() or not self.offer_state.is_paused():
            return

        balance = await self.balance_fetcher(self.user_client)
        if balance is None:
            return

        required = await self.repo.get_offer_price()
        if balance >= required:
            if await self.offer_state.resume():
                await self.notifier.notify_offer_resumed(balance, required)

    def _log_first_scan_if_needed(self) -> None:
        if self._first_scan_logged:
            return
        logger.info(
            "Birinchi tekshiruv: %d ta mavjud listing topildi, ular orasidan %d tasi MAX_NFT_PRICE dan past",
            self._cycle_new_count, self._cycle_within_budget_count,
        )
        self._first_scan_logged = True

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
