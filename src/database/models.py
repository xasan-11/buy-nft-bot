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
"""
