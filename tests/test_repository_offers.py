from __future__ import annotations

from src.database.repository import OfferStatus


async def test_offer_settings_defaults(repo):
    assert await repo.get_offer_level() == 1
    assert await repo.get_offer_nft_count() == 3
    assert await repo.get_offer_price() == 125
    assert await repo.get_offer_expiry_hours() == 6
    assert await repo.get_offer_active() is False
    assert await repo.get_offer_sent_count() == 0


async def test_offer_settings_are_persisted(repo):
    await repo.set_offer_level(2)
    await repo.set_offer_nft_count(5)
    await repo.set_offer_price(300)
    await repo.set_offer_expiry_hours(24)

    assert await repo.get_offer_level() == 2
    assert await repo.get_offer_nft_count() == 5
    assert await repo.get_offer_price() == 300
    assert await repo.get_offer_expiry_hours() == 24


async def test_start_offer_run_resets_sent_count(repo):
    await repo.start_offer_run()
    assert await repo.get_offer_active() is True
    assert await repo.get_offer_sent_count() == 0


async def test_stop_offer_run_only_clears_active_flag(repo):
    await repo.start_offer_run()
    await repo.increment_offer_sent_count()
    await repo.stop_offer_run()

    assert await repo.get_offer_active() is False
    assert await repo.get_offer_sent_count() == 1  # not reset by stop


async def test_increment_offer_sent_count_never_auto_stops(repo):
    """No target-count concept at all — a run only ever ends via
    stop_offer_run(), never on its own no matter how many offers go out."""
    await repo.start_offer_run()

    for _ in range(10):
        await repo.increment_offer_sent_count()

    assert await repo.get_offer_active() is True
    assert await repo.get_offer_sent_count() == 10


async def test_offer_selected_gift_types_defaults_empty(repo):
    assert await repo.get_offer_selected_gift_types() == set()


async def test_offer_selected_gift_types_round_trips(repo):
    await repo.set_offer_selected_gift_types({5, 1, 42})

    assert await repo.get_offer_selected_gift_types() == {1, 5, 42}


async def test_offer_selected_gift_types_can_be_cleared(repo):
    await repo.set_offer_selected_gift_types({5, 1})
    await repo.set_offer_selected_gift_types(set())

    assert await repo.get_offer_selected_gift_types() == set()


async def test_claim_offer_slot_dedups_by_slug(repo):
    claimed_first = await repo.claim_offer_slot(
        offer_id=1, slug="Gift-1", gift_id=42, price_stars=125,
        duration_seconds=21600, expires_at="2026-01-01T00:00:00+00:00",
    )
    claimed_second = await repo.claim_offer_slot(
        offer_id=2, slug="Gift-1", gift_id=42, price_stars=125,
        duration_seconds=21600, expires_at="2026-01-01T00:00:00+00:00",
    )

    assert claimed_first is True
    assert claimed_second is False


async def test_mark_offer_failed_and_stats(repo):
    await repo.claim_offer_slot(
        offer_id=1, slug="Gift-2", gift_id=42, price_stars=125,
        duration_seconds=21600, expires_at="2026-01-01T00:00:00+00:00",
    )
    await repo.mark_offer_failed("Gift-2", "RESELL_STARS_TOO_FEW")

    stats = await repo.get_offer_stats()
    assert stats["total"] == 1
    assert stats["failed"] == 1
    assert stats["pending"] == 0


async def test_update_offer_status(repo):
    await repo.claim_offer_slot(
        offer_id=1, slug="Gift-3", gift_id=42, price_stars=125,
        duration_seconds=21600, expires_at="2026-01-01T00:00:00+00:00",
    )
    await repo.update_offer_status("Gift-3", OfferStatus.ACCEPTED)

    stats = await repo.get_offer_stats()
    assert stats["accepted"] == 1


async def test_expire_stale_offers_flips_only_past_expiry(repo):
    await repo.claim_offer_slot(
        offer_id=1, slug="Gift-expired", gift_id=42, price_stars=125,
        duration_seconds=1, expires_at="2000-01-01T00:00:00+00:00",
    )
    await repo.claim_offer_slot(
        offer_id=2, slug="Gift-future", gift_id=42, price_stars=125,
        duration_seconds=999999, expires_at="2999-01-01T00:00:00+00:00",
    )

    flipped = await repo.expire_stale_offers()

    assert flipped == 1
    stats = await repo.get_offer_stats()
    assert stats["expired"] == 1
    assert stats["pending"] == 1
