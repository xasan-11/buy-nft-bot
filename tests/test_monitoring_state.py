from src.database.repository import MARKET_MONITORING_KEY, STATUS_RUNNING, STATUS_STOPPED
from src.monitoring.channel_monitor import MonitorState


async def test_monitoring_defaults_to_stopped(repo):
    assert await repo.get_monitoring_status() == STATUS_STOPPED


async def test_sstart_sets_running_and_persists(repo):
    state = MonitorState(repo)
    await state.load()
    await state.start()
    assert state.is_running()
    assert await repo.get_monitoring_status() == STATUS_RUNNING


async def test_stop_sets_stopped_and_persists(repo):
    state = MonitorState(repo)
    await state.load()
    await state.start()
    await state.stop()
    assert not state.is_running()
    assert await repo.get_monitoring_status() == STATUS_STOPPED


async def test_state_reloads_persisted_running_value(repo):
    state1 = MonitorState(repo)
    await state1.load()
    await state1.start()

    state2 = MonitorState(repo)
    await state2.load()
    assert state2.is_running()


async def test_channel_and_market_states_are_independent(repo):
    channel_state = MonitorState(repo)
    market_state = MonitorState(repo, key=MARKET_MONITORING_KEY)
    await channel_state.load()
    await market_state.load()

    await channel_state.start()

    assert channel_state.is_running()
    assert not market_state.is_running()
    assert await repo.get_monitoring_status() == STATUS_RUNNING
    assert await repo.get_setting(MARKET_MONITORING_KEY) == STATUS_STOPPED


async def test_stopping_channel_does_not_affect_market(repo):
    channel_state = MonitorState(repo)
    market_state = MonitorState(repo, key=MARKET_MONITORING_KEY)
    await channel_state.load()
    await market_state.load()

    await market_state.start()
    await channel_state.stop()

    assert not channel_state.is_running()
    assert market_state.is_running()
