from __future__ import annotations

from src.monitoring.offer_state import OfferState


async def test_default_start_is_unbounded_and_stays_active(repo):
    state = OfferState(repo)
    await state.load()

    await state.start()

    assert state.is_active() is True
    for _ in range(20):
        await state.record_sent()
        assert state.is_active() is True
    assert state.sent == 20


async def test_stop_ends_an_unbounded_run_immediately(repo):
    state = OfferState(repo)
    await state.load()
    await state.start()

    await state.record_sent()
    await state.stop()

    assert state.is_active() is False


async def test_inactive_by_default(repo):
    state = OfferState(repo)
    await state.load()

    assert state.is_active() is False


async def test_state_survives_reload(repo):
    state = OfferState(repo)
    await state.load()
    await state.start()
    await state.record_sent()

    reloaded = OfferState(repo)
    await reloaded.load()

    assert reloaded.is_active() is True
    assert reloaded.sent == 1


async def test_not_paused_by_default(repo):
    state = OfferState(repo)
    await state.load()

    assert state.is_paused() is False


async def test_pause_sets_flag_and_returns_true_for_first_caller(repo):
    state = OfferState(repo)
    await state.load()
    await state.start()

    result = await state.pause()

    assert result is True
    assert state.is_paused() is True


async def test_pause_is_idempotent_only_first_caller_gets_true(repo):
    """Guards the exact scenario several concurrently-scanned listings all
    hitting BALANCE_TOO_LOW at once — only one of them should ever think it
    needs to send the "paused" notification."""
    state = OfferState(repo)
    await state.load()
    await state.start()

    first = await state.pause()
    second = await state.pause()
    third = await state.pause()

    assert first is True
    assert second is False
    assert third is False


async def test_resume_clears_flag_and_returns_true_for_first_caller(repo):
    state = OfferState(repo)
    await state.load()
    await state.start()
    await state.pause()

    result = await state.resume()

    assert result is True
    assert state.is_paused() is False


async def test_resume_is_idempotent_only_first_caller_gets_true(repo):
    state = OfferState(repo)
    await state.load()
    await state.start()
    await state.pause()

    first = await state.resume()
    second = await state.resume()

    assert first is True
    assert second is False


async def test_resume_without_pause_is_a_noop(repo):
    state = OfferState(repo)
    await state.load()
    await state.start()

    result = await state.resume()

    assert result is False
    assert state.is_paused() is False


async def test_starting_a_fresh_run_clears_a_stale_pause(repo):
    state = OfferState(repo)
    await state.load()
    await state.start()
    await state.pause()
    await state.stop()

    await state.start()  # admin taps "Start" again

    assert state.is_paused() is False


async def test_pause_state_survives_reload(repo):
    state = OfferState(repo)
    await state.load()
    await state.start()
    await state.pause()

    reloaded = OfferState(repo)
    await reloaded.load()

    assert reloaded.is_paused() is True
