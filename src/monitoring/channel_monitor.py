from __future__ import annotations

import logging
from typing import Callable, Optional

from ..database.repository import (
    ListingStatus,
    MONITORING_KEY,
    PurchaseResult,
    Repository,
    STATUS_RUNNING,
    STATUS_STOPPED,
)
from ..marketplace.purchase import execute_purchase
from .nft_detector import detect_listing

logger = logging.getLogger("monitoring")


class MonitorState:
    """Persistent RUNNING/STOPPED flag, backed by application_settings so it
    survives a restart, with an in-memory mirror for fast checks on the hot
    path (every purchase re-checks this immediately before paying).

    Bound to a settings key so channel monitoring and market monitoring can
    each have their own independent, persisted RUNNING/STOPPED state — one
    can be RUNNING while the other is STOPPED. Defaults to the original
    channel-monitoring key so existing callers/tests are unaffected.
    """

    def __init__(self, repo: Repository, key: str = MONITORING_KEY):
        self._repo = repo
        self._key = key
        self._running = False

    async def load(self) -> None:
        status = await self._repo.get_setting(self._key, STATUS_STOPPED)
        self._running = status == STATUS_RUNNING

    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        self._running = True
        await self._repo.set_setting(self._key, STATUS_RUNNING)

    async def stop(self) -> None:
        self._running = False
        await self._repo.set_setting(self._key, STATUS_STOPPED)


class ChannelMonitor:
    def __init__(
        self,
        user_client,
        repo: Repository,
        state: MonitorState,
        max_price: int,
        notifier,
        purchase_executor: Callable = execute_purchase,
    ):
        self.user_client = user_client
        self.repo = repo
        self.state = state
        self.max_price = max_price
        self.notifier = notifier
        self.purchase_executor = purchase_executor
        self.channel_ids: set = set()

    async def refresh_channels(self) -> None:
        self.channel_ids = await self.repo.get_channel_ids()

    async def handle_deleted_message(self, channel_id: int, message_id: int) -> None:
        """Best-effort: if a tracked listing's post disappears before it was
        purchased, mark it expired so it is not attempted later."""
        row = await self.repo.get_listing_by_message(channel_id, message_id)
        if row and row["status"] in (ListingStatus.DETECTED, ListingStatus.ELIGIBLE):
            await self.repo.update_listing_status(row["slug"], ListingStatus.EXPIRED)

    async def handle_message(
        self, channel_id: int, channel_username: Optional[str], message_id: int, text: str
    ) -> None:
        # Pipeline step 0: nothing new starts once monitoring is stopped.
        if not self.state.is_running():
            return
        if channel_id not in self.channel_ids:
            return

        parsed = detect_listing(text or "")
        if parsed is None:
            return  # not reliably identifiable as an NFT/Gift listing

        # Atomic dedup/lock: only the caller that wins this insert proceeds.
        claimed = await self.repo.claim_listing(
            slug=parsed.slug,
            gift_id=None,
            channel_id=channel_id,
            message_id=message_id,
            price_stars=parsed.price_stars,
        )
        if not claimed:
            logger.info("Listing %s already processed, skipping duplicate", parsed.slug)
            return

        # Read live: MAX_NFT_PRICE is shared with market monitoring and can be
        # changed at any time via /setmaxprice, so it is never cached on self.
        max_price = await self.repo.get_max_price()

        if parsed.price_stars is None:
            await self.repo.update_listing_status(parsed.slug, ListingStatus.IGNORED_UNKNOWN_PRICE)
            await self.notifier.notify_ignored(
                parsed.slug, None, max_price, channel_username,
                "price could not be reliably determined",
            )
            return

        if parsed.price_stars > max_price:
            await self.repo.update_listing_status(parsed.slug, ListingStatus.IGNORED_PRICE)
            await self.notifier.notify_ignored(
                parsed.slug, parsed.price_stars, max_price, channel_username,
                "price exceeds maximum",
            )
            return

        await self.repo.update_listing_status(parsed.slug, ListingStatus.ELIGIBLE)

        # Re-check monitoring state before doing anything that leads to a
        # purchase (a /stop may have landed while we were parsing/claiming).
        if not self.state.is_running():
            await self.repo.update_listing_status(parsed.slug, ListingStatus.IGNORED_STOPPED)
            return

        await self.repo.update_listing_status(parsed.slug, ListingStatus.PURCHASE_ATTEMPTED)

        outcome = await self.purchase_executor(
            self.user_client, parsed.slug, max_price, self.state.is_running
        )

        if outcome.success:
            await self.repo.update_listing_status(
                parsed.slug, ListingStatus.PURCHASED, price_stars=outcome.price_stars
            )
            await self.repo.record_purchase_attempt(
                parsed.slug, outcome.price_stars, channel_id, PurchaseResult.SUCCESS, None
            )
            await self.notifier.notify_purchased(parsed.slug, outcome.price_stars, channel_username)
        else:
            await self.repo.update_listing_status(parsed.slug, ListingStatus.FAILED)
            await self.repo.record_purchase_attempt(
                parsed.slug,
                outcome.price_stars or parsed.price_stars,
                channel_id,
                PurchaseResult.FAILED,
                outcome.error,
            )
            await self.notifier.notify_failed(
                parsed.slug, outcome.price_stars or parsed.price_stars, channel_username, outcome.error
            )
