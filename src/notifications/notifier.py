from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("notifications")


class Notifier:
    """Sends notifications to the owner and every administrator via the
    control bot's own DM channel with them."""

    def __init__(self, bot_client, repo):
        self.bot_client = bot_client
        self.repo = repo

    async def _broadcast(self, text: str) -> None:
        rows = await self.repo.list_admins()
        for row in rows:
            try:
                await self.bot_client.send_message(row["user_id"], text)
            except Exception:
                logger.exception("Failed to notify admin %s", row["user_id"])

    async def notify_purchased(self, slug: str, price: int, channel_username: Optional[str]) -> None:
        await self._broadcast(
            "✅ NFT purchased\n"
            f"Price: {price} ⭐\n"
            f"Channel: @{channel_username or 'unknown'}\n"
            f"NFT ID: {slug}"
        )

    async def notify_ignored(
        self,
        slug: str,
        price: Optional[int],
        max_price: int,
        channel_username: Optional[str],
        reason: str,
    ) -> None:
        """Ignored listings are recorded in processed_listings/get_stats for
        /status, but deliberately do NOT send a Telegram message: a channel
        or a market scan can turn up hundreds of over-priced listings in a
        short time (this is the normal case, not an error), and broadcasting
        one message per listing floods the bot's own send rate limit — which
        previously starved out real command replies (e.g. /setmaxprice)
        behind a queue of FloodWait retries. Logged instead, at debug level
        since this fires very often by design.
        """
        logger.debug(
            "Ignored listing %s: price=%s limit=%s channel=%s reason=%s",
            slug, price, max_price, channel_username, reason,
        )

    async def notify_failed(
        self,
        slug: str,
        price: Optional[int],
        channel_username: Optional[str],
        reason: str,
    ) -> None:
        price_text = f"{price} ⭐" if price is not None else "unknown"
        await self._broadcast(
            "❌ NFT purchase failed\n"
            f"Price: {price_text}\n"
            f"Channel: @{channel_username or 'unknown'}\n"
            f"Reason: {reason}"
        )

    async def notify_monitoring_stopped(self) -> None:
        await self._broadcast("🛑 Monitoring stopped.")

    async def notify_monitoring_started(self) -> None:
        await self._broadcast("🟢 Monitoring started.")

    async def notify_market_monitoring_stopped(self) -> None:
        await self._broadcast("🛑 Market monitoring stopped.")

    async def notify_market_monitoring_started(self) -> None:
        await self._broadcast("🟢 Market monitoring started.")

    async def notify_listing_checked(self, name: str, price: Optional[int], link: str) -> None:
        """Verbose /startpo mode only: one message per listing seen during a
        market poll cycle, independent of the purchased/ignored/failed
        outcome. Off by default (see notify_ignored) — only sends when an
        admin has explicitly opted into it, since it fires very often."""
        price_text = f"{price} ⭐" if price is not None else "unknown"
        await self._broadcast(
            f"🔍 Tekshirilmoqda: {name}\n"
            f"Narx: {price_text}\n"
            f"Havola: {link}"
        )
