SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS admins (
    user_id INTEGER PRIMARY KEY,
    is_owner INTEGER NOT NULL DEFAULT 0,
    added_by INTEGER,
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS monitored_channels (
    channel_id INTEGER PRIMARY KEY,
    channel_username TEXT,
    added_by INTEGER,
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processed_listings (
    slug TEXT PRIMARY KEY,
    gift_id INTEGER,
    channel_id INTEGER,
    message_id INTEGER,
    price_stars INTEGER,
    status TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS purchase_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL,
    price_stars INTEGER,
    channel_id INTEGER,
    attempted_at TEXT NOT NULL,
    result TEXT NOT NULL,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS application_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Fresh installs get the final (user_id, slug) unique shape directly. An
-- existing database that still has the old single-tenant slug-UNIQUE shape
-- is migrated in place by Repository._migrate_star_gift_offers_for_multi_tenant.
CREATE TABLE IF NOT EXISTS star_gift_offers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    offer_id INTEGER NOT NULL,
    slug TEXT NOT NULL,
    gift_id INTEGER,
    price_stars INTEGER NOT NULL,
    duration_seconds INTEGER NOT NULL,
    status TEXT NOT NULL,
    error_message TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    owner_peer_id INTEGER,
    UNIQUE(user_id, slug)
);

-- One row per Telegram user who has ever pressed /start or completed
-- /login on the control bot — the home for that user's own personal
-- Telegram session (StringSession) and their own offer-sending settings,
-- so every user's login and offer configuration is fully independent (see
-- the multi-tenant rework in telegram/user_manager.py and
-- monitoring/offer_registry.py). The owner is just another row here for
-- offer purposes, in addition to the elevated admin/global capabilities
-- tracked in the `admins` table above.
CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    session_string TEXT NOT NULL DEFAULT '',
    phone TEXT,
    offer_level INTEGER NOT NULL DEFAULT 1,
    offer_nft_count INTEGER NOT NULL DEFAULT 3,
    offer_price_stars INTEGER NOT NULL DEFAULT 125,
    offer_expiry_hours INTEGER NOT NULL DEFAULT 6,
    offer_active INTEGER NOT NULL DEFAULT 0,
    offer_sent_count INTEGER NOT NULL DEFAULT 0,
    offer_selected_gift_types TEXT NOT NULL DEFAULT '',
    offer_paused INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""
