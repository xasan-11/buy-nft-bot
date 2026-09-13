from __future__ import annotations

from src.monitoring.offer_registry import OfferRegistry

OWNER_ID = 111
OTHER_ID = 222


async def test_get_returns_the_same_instance_for_the_same_user(repo):
    registry = OfferRegistry(repo)

    first = await registry.get(OWNER_ID)
    second = await registry.get(OWNER_ID)

    assert first is second


async def test_get_is_independent_per_user(repo):
    registry = OfferRegistry(repo)

    owner_state = await registry.get(OWNER_ID)
    other_state = await registry.get(OTHER_ID)
    await owner_state.start()

    assert owner_state.is_active() is True
    assert other_state.is_active() is False


async def test_active_only_lists_active_users(repo):
    registry = OfferRegistry(repo)
    owner_state = await registry.get(OWNER_ID)
    await registry.get(OTHER_ID)
    await owner_state.start()

    active = await registry.active()

    assert [uid for uid, _ in active] == [OWNER_ID]


async def test_preload_active_resumes_runs_from_a_previous_process(repo):
    await repo.start_user_offer_run(OTHER_ID)

    registry = OfferRegistry(repo)
    await registry.preload_active()

    active_ids = {uid for uid, _ in await registry.active()}
    assert active_ids == {OTHER_ID}
