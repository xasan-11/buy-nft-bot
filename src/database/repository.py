from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Optional

import aiosqlite

from .models import SCHEMA


class ListingStatus:
    DETECTED = "DETECTED"
    ELIGIBLE = "ELIGIBLE"
    IGNORED_PRICE = "IGNORED_PRICE"
    IGNORED_UNKNOWN_PRICE = "IGNORED_UNKNOWN_PRICE"
    IGNORED_STOPPED = "IGNORED_STOPPED"
    PURCHASE_ATTEMPTED = "PURCHASE_ATTEMPTED"
    PURCHASED = "PURCHASED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class PurchaseResult:
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class OfferStatus:
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


MONITORING_KEY = "monitoring_status"
MARKET_MONITORING_KEY = "market_monitoring_status"
MAX_PRICE_KEY = "max_nft_price"
MARKET_POLL_CONCURRENCY_KEY = "market_poll_concurrency"
VERBOSE_POLL_LOG_KEY = "verbose_poll_log"
VERBOSE_POLL_INTERVAL_KEY = "verbose_poll_interval"
OFFER_LEVEL_KEY = "offer_seller_level"
OFFER_NFT_COUNT_KEY = "offer_seller_max_nft_count"
OFFER_PRICE_KEY = "offer_price_stars"
OFFER_EXPIRY_HOURS_KEY = "offer_expiry_hours"
OFFER_ACTIVE_KEY = "offer_active"
OFFER_SENT_KEY = "offer_sent_count"
OFFER_SELECTED_GIFT_TYPES_KEY = "offer_selected_gift_types"
OFFER_PAUSED_KEY = "offer_paused"
STATUS_RUNNING = "RUNNING"
STATUS_STOPPED = "STOPPED"

# payments.sendStarGiftOffer only accepts one of these exact durations (in
# seconds) — see core.telegram.org/method/payments.sendStarGiftOffer.
ALLOWED_OFFER_HOURS = (6, 12, 24, 36, 48, 72)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


class Repository:
    """Async SQLite repository shared by the monitor and the control bot.

    A single aiosqlite connection is used serially, which combined with the
    UNIQUE primary key on processed_listings.slug is what actually prevents
    two concurrent handlers from purchasing the same listing twice: the
    second INSERT simply fails and that caller backs off (see claim_listing).
    """

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(
        self,
        owner_id: int,
        default_max_price: int = 200,
        default_market_poll_concurrency: int = 3,
    ) -> None:
        # timeout=8 gives Python's own sqlite3 busy-wait the same ceiling as
        # the PRAGMA below — belt and suspenders, since aiosqlite just
        # forwards this straight to sqlite3.connect().
        self._conn = await aiosqlite.connect(self._db_path, timeout=8.0)
        self._conn.row_factory = aiosqlite.Row
        # Set before executescript (which would otherwise run its own first
        # statement, the schema's PRAGMA journal_mode=WAL, inside an
        # implicit transaction boundary) so a concurrent reader/writer never
        # sees this connection with default (DELETE) journal mode even for
        # an instant, and so busy_timeout is guaranteed to be in effect for
        # every statement that follows, including schema creation itself.
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA busy_timeout=8000")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()
        await self._migrate()
        await self._ensure_owner(owner_id)
        await self._ensure_default_setting(MONITORING_KEY, STATUS_STOPPED)
        await self._ensure_default_setting(MARKET_MONITORING_KEY, STATUS_STOPPED)
        # MAX_NFT_PRICE from .env only seeds this on first run — after that the
        # database value is authoritative and can be changed live via /setmaxprice.
        await self._ensure_default_setting(MAX_PRICE_KEY, str(default_max_price))
        await self._ensure_default_setting(
            MARKET_POLL_CONCURRENCY_KEY, str(default_market_poll_concurrency)
        )
        await self._ensure_default_setting(VERBOSE_POLL_LOG_KEY, "OFF")
        await self._ensure_default_setting(VERBOSE_POLL_INTERVAL_KEY, "5")
        await self._ensure_default_setting(OFFER_LEVEL_KEY, "1")
        await self._ensure_default_setting(OFFER_NFT_COUNT_KEY, "3")
        await self._ensure_default_setting(OFFER_PRICE_KEY, "125")
        await self._ensure_default_setting(OFFER_EXPIRY_HOURS_KEY, "6")
        await self._ensure_default_setting(OFFER_ACTIVE_KEY, "OFF")
        await self._ensure_default_setting(OFFER_SENT_KEY, "0")
        await self._ensure_default_setting(OFFER_SELECTED_GIFT_TYPES_KEY, "")
        await self._ensure_default_setting(OFFER_PAUSED_KEY, "OFF")

    async def _migrate(self) -> None:
        """Adds columns introduced after the original schema to an existing
        database in place, without touching the CREATE TABLE IF NOT EXISTS
        statements in models.py (which stay correct for brand-new installs)."""
        cur = await self._conn.execute("PRAGMA table_info(processed_listings)")
        columns = {row["name"] for row in await cur.fetchall()}
        if "source" not in columns:
            await self._conn.execute(
                "ALTER TABLE processed_listings ADD COLUMN source TEXT NOT NULL DEFAULT 'channel'"
            )
            await self._conn.commit()

        cur = await self._conn.execute("PRAGMA table_info(star_gift_offers)")
        offer_columns = {row["name"] for row in await cur.fetchall()}
        if "owner_peer_id" not in offer_columns:
            await self._conn.execute(
                "ALTER TABLE star_gift_offers ADD COLUMN owner_peer_id INTEGER"
            )
            await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def _ensure_owner(self, owner_id: int) -> None:
        await self._conn.execute(
            """
            INSERT INTO admins(user_id, is_owner, added_by, added_at)
            VALUES (?, 1, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET is_owner = 1
            """,
            (owner_id, owner_id, _now()),
        )
        await self._conn.commit()

    async def _ensure_default_setting(self, key: str, value: str) -> None:
        await self._conn.execute(
            "INSERT OR IGNORE INTO application_settings(key, value) VALUES (?, ?)",
            (key, value),
        )
        await self._conn.commit()

    # ---------------------------------------------------------------- admins
    async def is_admin(self, user_id: int) -> bool:
        cur = await self._conn.execute(
            "SELECT 1 FROM admins WHERE user_id = ?", (user_id,)
        )
        return await cur.fetchone() is not None

    async def is_owner(self, user_id: int) -> bool:
        cur = await self._conn.execute(
            "SELECT 1 FROM admins WHERE user_id = ? AND is_owner = 1", (user_id,)
        )
        return await cur.fetchone() is not None

    async def add_admin(self, user_id: int, added_by: int) -> bool:
        if await self.is_admin(user_id):
            return False
        await self._conn.execute(
            "INSERT INTO admins(user_id, is_owner, added_by, added_at) VALUES (?, 0, ?, ?)",
            (user_id, added_by, _now()),
        )
        await self._conn.commit()
        return True

    async def remove_admin(self, user_id: int) -> str:
        """Returns 'owner', 'not_found', or 'removed'."""
        if await self.is_owner(user_id):
            return "owner"
        if not await self.is_admin(user_id):
            return "not_found"
        await self._conn.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
        await self._conn.commit()
        return "removed"

    async def list_admins(self):
        cur = await self._conn.execute(
            "SELECT user_id, is_owner, added_at FROM admins ORDER BY is_owner DESC, added_at ASC"
        )
        return await cur.fetchall()

    # -------------------------------------------------------------- channels
    async def add_channel(self, channel_id: int, username: Optional[str], added_by: int) -> bool:
        cur = await self._conn.execute(
            "SELECT 1 FROM monitored_channels WHERE channel_id = ?", (channel_id,)
        )
        if await cur.fetchone():
            return False
        await self._conn.execute(
            "INSERT INTO monitored_channels(channel_id, channel_username, added_by, added_at) VALUES (?, ?, ?, ?)",
            (channel_id, username, added_by, _now()),
        )
        await self._conn.commit()
        return True

    async def remove_channel(self, channel_id: int) -> bool:
        cur = await self._conn.execute(
            "DELETE FROM monitored_channels WHERE channel_id = ?", (channel_id,)
        )
        await self._conn.commit()
        return cur.rowcount > 0

    async def list_channels(self):
        cur = await self._conn.execute(
            "SELECT channel_id, channel_username, added_at FROM monitored_channels ORDER BY added_at ASC"
        )
        return await cur.fetchall()

    async def get_channel_ids(self) -> set:
        rows = await self.list_channels()
        return {row["channel_id"] for row in rows}

    # ------------------------------------------------------- settings/state
    async def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        cur = await self._conn.execute(
            "SELECT value FROM application_settings WHERE key = ?", (key,)
        )
        row = await cur.fetchone()
        return row["value"] if row else default

    async def set_setting(self, key: str, value: str) -> None:
        await self._conn.execute(
            """
            INSERT INTO application_settings(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        await self._conn.commit()

    async def get_monitoring_status(self) -> str:
        return await self.get_setting(MONITORING_KEY, STATUS_STOPPED)

    async def set_monitoring_status(self, status: str) -> None:
        await self.set_setting(MONITORING_KEY, status)

    async def get_max_price(self) -> int:
        """Live, admin-changeable maximum auto-purchase price. Shared by
        channel monitoring and market monitoring — changing it here affects
        both immediately, since neither caches it."""
        value = await self.get_setting(MAX_PRICE_KEY)
        return int(value) if value is not None else 200

    async def set_max_price(self, value: int) -> None:
        await self.set_setting(MAX_PRICE_KEY, str(value))

    async def get_market_poll_concurrency(self) -> int:
        """Live, admin-changeable cap on how many payments.getResaleStarGifts
        requests the market scanner is allowed to have in flight at once."""
        value = await self.get_setting(MARKET_POLL_CONCURRENCY_KEY)
        return int(value) if value is not None else 3

    async def set_market_poll_concurrency(self, value: int) -> None:
        await self.set_setting(MARKET_POLL_CONCURRENCY_KEY, str(value))

    async def get_verbose_poll_log(self) -> bool:
        """Live /startpo (ON) / /stopo (OFF) toggle: when ON, the market
        scanner sends a Telegram message for every listing it checks each
        cycle, on top of (not instead of) the normal purchased/failed
        notifications. Persisted so it survives a restart."""
        value = await self.get_setting(VERBOSE_POLL_LOG_KEY, "OFF")
        return value == "ON"

    async def set_verbose_poll_log(self, enabled: bool) -> None:
        await self.set_setting(VERBOSE_POLL_LOG_KEY, "ON" if enabled else "OFF")

    async def get_verbose_poll_interval(self) -> int:
        """Seconds between aggregated verbose "checked" messages (0 means
        send one message per listing immediately, with no aggregation).
        Independent of MARKET_POLL_INTERVAL_SECONDS — this only controls
        how often verbose notifications are *flushed*, not how often
        Telegram is actually polled for listings."""
        value = await self.get_setting(VERBOSE_POLL_INTERVAL_KEY)
        return int(value) if value is not None else 5

    async def set_verbose_poll_interval(self, seconds: int) -> None:
        await self.set_setting(VERBOSE_POLL_INTERVAL_KEY, str(seconds))

    # --------------------------------------------------- offer configuration
    async def get_offer_level(self) -> int:
        value = await self.get_setting(OFFER_LEVEL_KEY)
        return int(value) if value is not None else 1

    async def set_offer_level(self, value: int) -> None:
        await self.set_setting(OFFER_LEVEL_KEY, str(value))

    async def get_offer_nft_count(self) -> int:
        """Sellers must own FEWER than this many gifts to be offer-eligible."""
        value = await self.get_setting(OFFER_NFT_COUNT_KEY)
        return int(value) if value is not None else 3

    async def set_offer_nft_count(self, value: int) -> None:
        await self.set_setting(OFFER_NFT_COUNT_KEY, str(value))

    async def get_offer_price(self) -> int:
        value = await self.get_setting(OFFER_PRICE_KEY)
        return int(value) if value is not None else 125

    async def set_offer_price(self, value: int) -> None:
        await self.set_setting(OFFER_PRICE_KEY, str(value))

    async def get_offer_expiry_hours(self) -> int:
        value = await self.get_setting(OFFER_EXPIRY_HOURS_KEY)
        return int(value) if value is not None else 6

    async def set_offer_expiry_hours(self, hours: int) -> None:
        await self.set_setting(OFFER_EXPIRY_HOURS_KEY, str(hours))

    # -------------------------------------------------------- offer run state
    async def get_offer_active(self) -> bool:
        return await self.get_setting(OFFER_ACTIVE_KEY, "OFF") == "ON"

    async def get_offer_sent_count(self) -> int:
        value = await self.get_setting(OFFER_SENT_KEY)
        return int(value) if value is not None else 0

    async def start_offer_run(self) -> None:
        """Always unbounded — runs until "🛑 Offer to'xtatish" is tapped.
        There is no target-count/"send N then stop" concept at all."""
        await self.set_setting(OFFER_ACTIVE_KEY, "ON")
        await self.set_setting(OFFER_SENT_KEY, "0")

    async def stop_offer_run(self) -> None:
        await self.set_setting(OFFER_ACTIVE_KEY, "OFF")

    async def increment_offer_sent_count(self) -> int:
        sent = await self.get_offer_sent_count() + 1
        await self.set_setting(OFFER_SENT_KEY, str(sent))
        return sent

    async def get_offer_selected_gift_types(self) -> set:
        value = await self.get_setting(OFFER_SELECTED_GIFT_TYPES_KEY, "")
        if not value:
            return set()
        return {int(x) for x in value.split(",") if x}

    async def set_offer_selected_gift_types(self, gift_ids) -> None:
        await self.set_setting(
            OFFER_SELECTED_GIFT_TYPES_KEY, ",".join(str(g) for g in sorted(gift_ids))
        )

    async def get_offer_paused(self) -> bool:
        """True once a BALANCE_TOO_LOW response has paused the auto-offer
        pipeline — see OfferState.pause()/resume() for the actual
        transition logic (this is just the persisted flag)."""
        return await self.get_setting(OFFER_PAUSED_KEY, "OFF") == "ON"

    async def set_offer_paused(self, paused: bool) -> None:
        await self.set_setting(OFFER_PAUSED_KEY, "ON" if paused else "OFF")

    # -------------------------------------------------------------- offers
    async def claim_offer_slot(
        self, offer_id: int, slug: str, gift_id: Optional[int], price_stars: int,
        duration_seconds: int, expires_at: str, owner_peer_id: Optional[int] = None,
    ) -> bool:
        """Atomically claims this slug for an offer attempt — a slug is only
        ever offered on once, mirroring claim_listing's dedup-via-INSERT
        pattern. Returns False if this slug already has an offer record.

        `owner_peer_id` (a telethon.utils.get_peer_id-style marked id) is
        recorded so a later decline can find every other still-PENDING
        offer to the same seller (see count_pending_offers_for_peer) before
        removing them from the "Offer" chat folder.
        """
        try:
            await self._conn.execute(
                """
                INSERT INTO star_gift_offers(
                    offer_id, slug, gift_id, price_stars, duration_seconds,
                    status, created_at, expires_at, updated_at, owner_peer_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    offer_id, slug, gift_id, price_stars, duration_seconds,
                    OfferStatus.PENDING, _now(), expires_at, _now(), owner_peer_id,
                ),
            )
            await self._conn.commit()
            return True
        except aiosqlite.IntegrityError:
            return False

    async def get_offer_owner_peer_id(self, slug: str) -> Optional[int]:
        cur = await self._conn.execute(
            "SELECT owner_peer_id FROM star_gift_offers WHERE slug = ?", (slug,)
        )
        row = await cur.fetchone()
        return row["owner_peer_id"] if row and row["owner_peer_id"] is not None else None

    async def count_pending_offers_for_peer(self, owner_peer_id: int) -> int:
        cur = await self._conn.execute(
            "SELECT COUNT(*) FROM star_gift_offers WHERE owner_peer_id = ? AND status = ?",
            (owner_peer_id, OfferStatus.PENDING),
        )
        row = await cur.fetchone()
        return row[0] if row else 0

    async def mark_offer_failed(self, slug: str, error_message: str) -> None:
        await self._conn.execute(
            "UPDATE star_gift_offers SET status = ?, error_message = ?, updated_at = ? WHERE slug = ?",
            (OfferStatus.FAILED, error_message, _now(), slug),
        )
        await self._conn.commit()

    async def update_offer_status(self, slug: str, status: str) -> None:
        await self._conn.execute(
            "UPDATE star_gift_offers SET status = ?, updated_at = ? WHERE slug = ?",
            (status, _now(), slug),
        )
        await self._conn.commit()

    async def expire_stale_offers(self) -> int:
        """Marks every still-PENDING offer whose expiry has passed as
        EXPIRED. Telegram auto-refunds the reserved Stars on its own side;
        this only keeps our local record in sync. Returns how many rows
        were flipped."""
        cur = await self._conn.execute(
            "UPDATE star_gift_offers SET status = ?, updated_at = ? WHERE status = ? AND expires_at <= ?",
            (OfferStatus.EXPIRED, _now(), OfferStatus.PENDING, _now()),
        )
        await self._conn.commit()
        return cur.rowcount

    async def get_offer_stats(self) -> dict:
        async def scalar(query: str, params: tuple = ()) -> int:
            cur = await self._conn.execute(query, params)
            row = await cur.fetchone()
            return row[0] if row else 0

        total = await scalar("SELECT COUNT(*) FROM star_gift_offers")
        pending = await scalar(
            "SELECT COUNT(*) FROM star_gift_offers WHERE status = ?", (OfferStatus.PENDING,)
        )
        accepted = await scalar(
            "SELECT COUNT(*) FROM star_gift_offers WHERE status = ?", (OfferStatus.ACCEPTED,)
        )
        declined = await scalar(
            "SELECT COUNT(*) FROM star_gift_offers WHERE status = ?", (OfferStatus.DECLINED,)
        )
        expired = await scalar(
            "SELECT COUNT(*) FROM star_gift_offers WHERE status = ?", (OfferStatus.EXPIRED,)
        )
        failed = await scalar(
            "SELECT COUNT(*) FROM star_gift_offers WHERE status = ?", (OfferStatus.FAILED,)
        )
        return {
            "total": total,
            "pending": pending,
            "accepted": accepted,
            "declined": declined,
            "expired": expired,
            "failed": failed,
        }

    # -------------------------------------------------------------- listings
    async def claim_listing(
        self,
        slug: str,
        gift_id: Optional[int],
        channel_id: Optional[int],
        message_id: Optional[int],
        price_stars: Optional[int],
        source: str = "channel",
    ) -> bool:
        """Atomically claims a listing. Returns False if already processed.

        This is also what prevents the same gift being bought twice when it
        is both posted in a monitored channel and turned up by the market
        scanner: whichever caller's INSERT wins owns the slug, regardless of
        `source`.
        """
        try:
            await self._conn.execute(
                """
                INSERT INTO processed_listings(
                    slug, gift_id, channel_id, message_id, price_stars,
                    status, detected_at, updated_at, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    slug, gift_id, channel_id, message_id, price_stars,
                    ListingStatus.DETECTED, _now(), _now(), source,
                ),
            )
            await self._conn.commit()
            return True
        except aiosqlite.IntegrityError:
            return False

    async def get_listing(self, slug: str):
        cur = await self._conn.execute(
            "SELECT * FROM processed_listings WHERE slug = ?", (slug,)
        )
        return await cur.fetchone()

    async def get_listing_by_message(self, channel_id: int, message_id: int):
        cur = await self._conn.execute(
            "SELECT * FROM processed_listings WHERE channel_id = ? AND message_id = ?",
            (channel_id, message_id),
        )
        return await cur.fetchone()

    async def update_listing_status(
        self, slug: str, status: str, price_stars: Optional[int] = None
    ) -> None:
        if price_stars is not None:
            await self._conn.execute(
                "UPDATE processed_listings SET status = ?, price_stars = ?, updated_at = ? WHERE slug = ?",
                (status, price_stars, _now(), slug),
            )
        else:
            await self._conn.execute(
                "UPDATE processed_listings SET status = ?, updated_at = ? WHERE slug = ?",
                (status, _now(), slug),
            )
        await self._conn.commit()

    async def record_purchase_attempt(
        self,
        slug: str,
        price_stars: Optional[int],
        channel_id: int,
        result: str,
        error_message: Optional[str],
    ) -> None:
        await self._conn.execute(
            """
            INSERT INTO purchase_attempts(slug, price_stars, channel_id, attempted_at, result, error_message)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (slug, price_stars, channel_id, _now(), result, error_message),
        )
        await self._conn.commit()

    async def get_stats(self) -> dict:
        async def scalar(query: str, params: tuple = ()) -> int:
            cur = await self._conn.execute(query, params)
            row = await cur.fetchone()
            return row[0] if row else 0

        total_detected = await scalar("SELECT COUNT(*) FROM processed_listings")
        eligible = await scalar(
            "SELECT COUNT(*) FROM processed_listings WHERE status NOT IN (?, ?)",
            (ListingStatus.IGNORED_PRICE, ListingStatus.IGNORED_UNKNOWN_PRICE),
        )
        attempts = await scalar("SELECT COUNT(*) FROM purchase_attempts")
        success = await scalar(
            "SELECT COUNT(*) FROM purchase_attempts WHERE result = ?", (PurchaseResult.SUCCESS,)
        )
        failed = await scalar(
            "SELECT COUNT(*) FROM purchase_attempts WHERE result = ?", (PurchaseResult.FAILED,)
        )
        channels = await scalar("SELECT COUNT(*) FROM monitored_channels")
        market_detected = await scalar(
            "SELECT COUNT(*) FROM processed_listings WHERE source = 'market'"
        )
        channel_detected = await scalar(
            "SELECT COUNT(*) FROM processed_listings WHERE source = 'channel'"
        )
        return {
            "total_detected": total_detected,
            "eligible": eligible,
            "attempts": attempts,
            "success": success,
            "failed": failed,
            "channels": channels,
            "market_detected": market_detected,
            "channel_detected": channel_detected,
        }
