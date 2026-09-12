async def test_default_max_price_seeded_from_env(repo):
    assert await repo.get_max_price() == 200


async def test_set_max_price_updates_value(repo):
    await repo.set_max_price(150)
    assert await repo.get_max_price() == 150


async def test_set_max_price_is_read_live_by_new_lookups(repo):
    await repo.set_max_price(75)
    await repo.set_max_price(300)
    assert await repo.get_max_price() == 300


async def test_default_market_poll_concurrency_seeded_from_env(repo):
    assert await repo.get_market_poll_concurrency() == 3


async def test_set_market_poll_concurrency_updates_value(repo):
    await repo.set_market_poll_concurrency(4)
    assert await repo.get_market_poll_concurrency() == 4


async def test_verbose_poll_log_defaults_off(repo):
    assert await repo.get_verbose_poll_log() is False


async def test_startpo_stopo_toggle_persists(repo):
    await repo.set_verbose_poll_log(True)
    assert await repo.get_verbose_poll_log() is True

    await repo.set_verbose_poll_log(False)
    assert await repo.get_verbose_poll_log() is False
