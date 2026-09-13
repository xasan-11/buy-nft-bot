from __future__ import annotations

import logging
from typing import Optional

from telethon import Button

logger = logging.getLogger("notifications")

MAX_ITEMS_PER_VERBOSE_MESSAGE = 20


class Notifier:
    """Sends notifications to the owner and every administrator via the
    control bot's own DM channel with them."""

    def __init__(self, bot_client, repo):
        self.bot_client = bot_client
        self.repo = repo

    async def _broadcast(self, text: str, buttons=None) -> None:
        rows = await self.repo.list_admins()
        for row in rows:
            try:
                await self.bot_client.send_message(row["user_id"], text, buttons=buttons)
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

    async def notify_offer_sent(
        self,
        name: str,
        price: int,
        duration_hours: int,
        nft_link: str,
        seller_profile_link: Optional[str] = None,
    ) -> None:
        text = (
            f"✅ Offer tashlandi: {name}\n"
            f"Narx: {price}⭐\n"
            f"Muddat: {duration_hours} soat\n"
            f"Havola: {nft_link}"
        )
        # Both buttons side by side in one row; the seller-profile one is
        # simply omitted when there's nothing to link to (no username and
        # no resolvable user id) rather than shown as a dead button.
        row = []
        if seller_profile_link:
            row.append(Button.url("👤 Sotuvchi profiliga o'tish", seller_profile_link))
        row.append(Button.url("🎁 NFT'ni ko'rish", nft_link))
        await self._broadcast(text, buttons=[row])

    async def notify_offer_failed(self, name: str, price: int, reason: Optional[str]) -> None:
        await self._broadcast(
            f"❌ Offer tashlanmadi: {name} — {price}⭐\nSabab: {reason or 'unknown'}"
        )

    async def notify_offer_paused(self) -> None:
        await self._broadcast(
            "⏸ Stars balansi tugadi. Offer tashlash to'xtatildi, balans tekshirilib turiladi."
        )

    async def notify_offer_resumed(self, balance: int, required: int) -> None:
        await self._broadcast(
            f"▶️ Balans yetarli ({required}⭐+), offer tashlash davom etmoqda. "
            f"(Joriy balans: {balance}⭐)"
        )

    async def notify_offer_folder_missing(self, folder_name: str) -> None:
        await self._broadcast(
            f"⚠️ '{folder_name}' nomli papka topilmadi. Iltimos Telegram'da shunday papka yarating."
        )

    async def notify_listing_checked(self, name: str, price: Optional[int], link: str) -> None:
        """Verbose /startpo <0> (immediate) mode only: one message per
        listing, sent the moment it's first seen — no aggregation. Off by
        default (see notify_ignored) — only sends when an admin has
        explicitly opted into it. Each listing is reported at most once in
        its lifetime, since the caller only invokes this for a listing that
        just won processed_listings' claim_listing race."""
        price_text = f"{price} ⭐" if price is not None else "unknown"
        await self._broadcast(
            f"🔍 Tekshirilmoqda: {name}\n"
            f"Narx: {price_text}\n"
            f"Havola: {link}"
        )

    async def notify_listings_checked_batch(self, items: list, interval: int) -> None:
        """Verbose /startpo <N> (N > 0) mode: one aggregated message per
        flush covering every listing newly claimed since the last flush.
        No message is sent when `items` is empty, so idle cycles don't
        spam admins. Truncated (with a "+X ta yana" tail) rather than sent
        as one arbitrarily long message if there are many listings."""
        if not items:
            return

        total = len(items)
        shown = items[:MAX_ITEMS_PER_VERBOSE_MESSAGE]
        lines = [f"🔍 Tekshiruv ({interval}s)", f"Topildi: {total} ta listing"]
        for i, (name, price, link) in enumerate(shown, start=1):
            price_text = f"{price} ⭐" if price is not None else "unknown"
            lines.append(f"{i}. {name} — {price_text} — {link}")
        remaining = total - len(shown)
        if remaining > 0:
            lines.append(f"... +{remaining} ta yana")
        await self._broadcast("\n".join(lines))
