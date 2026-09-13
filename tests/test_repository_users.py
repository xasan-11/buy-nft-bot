from __future__ import annotations

import aiosqlite

from src.database.repository import LEGACY_OWNER_SESSION_KEY, Repository

OWNER_ID = 111
OTHER_ID = 222


async def test_ensure_user_creates_row_with_defaults(repo):
    await repo.ensure_user(OTHER_ID)

    row = await repo.get_user(OTHER_ID)
    assert row is not None
    assert row["session_string"] == ""
    assert row["offer_level"] == 1
    assert row["offer_nft_count"] == 3
    assert row["offer_price_stars"] == 125
    assert row["offer_expiry_hours"] == 6
    assert row["offer_active"] == 0


async def test_per_user_offer_settings_are_independent(repo):
    await repo.set_user_offer_price(OWNER_ID, 300)
    await repo.set_user_offer_price(OTHER_ID, 50)

    assert await repo.get_user_offer_price(OWNER_ID) == 300
    assert await repo.get_user_offer_price(OTHER_ID) == 50


async def test_per_user_offer_selected_gift_types_round_trip(repo):
    await repo.set_user_offer_selected_gift_types(OTHER_ID, {5, 1, 42})
    assert await repo.get_user_offer_selected_gift_types(OTHER_ID) == {1, 5, 42}
    # A different user is unaffected.
    assert await repo.get_user_offer_selected_gift_types(OWNER_ID) == set()


async def test_start_stop_user_offer_run(repo):
    await repo.start_user_offer_run(OTHER_ID)
    assert await repo.get_user_offer_active(OTHER_ID) is True
    assert await repo.get_user_offer_sent_count(OTHER_ID) == 0

    await repo.increment_user_offer_sent_count(OTHER_ID)
    await repo.stop_user_offer_run(OTHER_ID)

    assert await repo.get_user_offer_active(OTHER_ID) is False
    assert await repo.get_user_offer_sent_count(OTHER_ID) == 1


async def test_user_session_round_trips(repo):
    await repo.set_user_session(OTHER_ID, "session-string-abc", phone="+998900000000")

    assert await repo.get_user_session(OTHER_ID) == "session-string-abc"
    row = await repo.get_user(OTHER_ID)
    assert row["phone"] == "+998900000000"


async def test_list_users_with_active_offer(repo):
    await repo.ensure_user(OTHER_ID)
    await repo.start_user_offer_run(OWNER_ID)

    active = await repo.list_users_with_active_offer()
    active_ids = {row["telegram_id"] for row in active}

    assert active_ids == {OWNER_ID}


async def test_owner_gets_a_users_row_seeded_from_legacy_settings(tmp_path):
    """Simulates a real pre-multi-tenant deployment: a database that
    already has admins/application_settings (with a session string under
    the legacy key, plus global offer settings) but no `users` table at
    all yet, i.e. exactly what an existing production data/app.db looks
    like before this upgrade. Connecting with the new code must seed the
    owner's `users` row from those values instead of forcing a fresh
    /login or resetting their offer configuration."""
    db_path = tmp_path / "legacy.db"
    legacy_conn = await aiosqlite.connect(db_path)
    await legacy_conn.executescript(
        """
        CREATE TABLE admins (
            user_id INTEGER PRIMARY KEY, is_owner INTEGER NOT NULL DEFAULT 0,
            added_by INTEGER, added_at TEXT NOT NULL
        );
        CREATE TABLE application_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """
    )
    await legacy_conn.execute(
        "INSERT INTO application_settings(key, value) VALUES (?, ?)",
        (LEGACY_OWNER_SESSION_KEY, "legacy-session-string"),
    )
    await legacy_conn.execute(
        "INSERT INTO application_settings(key, value) VALUES (?, ?)",
        ("offer_price_stars", "250"),
    )
    await legacy_conn.execute(
        "INSERT INTO application_settings(key, value) VALUES (?, ?)",
        ("offer_seller_level", "2"),
    )
    await legacy_conn.commit()
    await legacy_conn.close()

    r = Repository(db_path)
    await r.connect(OWNER_ID)
    try:
        row = await r.get_user(OWNER_ID)
        assert row is not None
        assert row["session_string"] == "legacy-session-string"
        assert row["offer_price_stars"] == 250
        assert row["offer_level"] == 2
    finally:
        await r.close()
