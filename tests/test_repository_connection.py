from __future__ import annotations

from src.database.repository import Repository


async def test_connect_enables_wal_journal_mode(tmp_path):
    repo = Repository(tmp_path / "wal_test.db")
    await repo.connect(owner_id=1)
    try:
        cur = await repo._conn.execute("PRAGMA journal_mode")
        row = await cur.fetchone()
        assert row[0].lower() == "wal"
    finally:
        await repo.close()


async def test_connect_sets_busy_timeout(tmp_path):
    repo = Repository(tmp_path / "timeout_test.db")
    await repo.connect(owner_id=1)
    try:
        cur = await repo._conn.execute("PRAGMA busy_timeout")
        row = await cur.fetchone()
        assert row[0] == 8000
    finally:
        await repo.close()


async def test_two_connections_to_same_db_do_not_deadlock_on_concurrent_writes(tmp_path):
    """Regression test for the "database is locked" crash: two Repository
    instances pointed at the same file (simulating two processes racing at
    startup) must both be able to write via busy_timeout's retry-and-wait
    instead of failing immediately."""
    db_path = tmp_path / "shared.db"

    repo_a = Repository(db_path)
    await repo_a.connect(owner_id=1)
    try:
        repo_b = Repository(db_path)
        await repo_b.connect(owner_id=2)
        try:
            # Both already ran _ensure_owner during connect() above without
            # raising OperationalError — that's the actual regression this
            # test guards. Do one more round of concurrent-ish writes for
            # good measure.
            await repo_a.set_max_price(111)
            await repo_b.set_max_price(222)
            assert await repo_b.get_max_price() == 222
        finally:
            await repo_b.close()
    finally:
        await repo_a.close()


async def test_connect_is_idempotent_across_restarts(tmp_path):
    """Simulates stopping and starting the bot several times in a row
    against the same on-disk database — each connect()/close() cycle must
    succeed without leaking a lock into the next one."""
    db_path = tmp_path / "restart_test.db"

    for _ in range(5):
        repo = Repository(db_path)
        await repo.connect(owner_id=1)
        assert await repo.is_owner(1)
        await repo.close()
