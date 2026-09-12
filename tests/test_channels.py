from tests.conftest import OWNER_ID


async def test_add_channel(repo):
    added = await repo.add_channel(-1001, "examplechannel", added_by=OWNER_ID)
    assert added
    ids = await repo.get_channel_ids()
    assert -1001 in ids


async def test_add_channel_twice_is_noop(repo):
    await repo.add_channel(-1001, "examplechannel", added_by=OWNER_ID)
    added_again = await repo.add_channel(-1001, "examplechannel", added_by=OWNER_ID)
    assert not added_again


async def test_remove_channel(repo):
    await repo.add_channel(-1001, "examplechannel", added_by=OWNER_ID)
    removed = await repo.remove_channel(-1001)
    assert removed
    assert -1001 not in await repo.get_channel_ids()


async def test_remove_unknown_channel_reports_false(repo):
    removed = await repo.remove_channel(-9999)
    assert not removed


async def test_list_channels(repo):
    await repo.add_channel(-1001, "examplechannel", added_by=OWNER_ID)
    await repo.add_channel(-1002, "otherchannel", added_by=OWNER_ID)
    rows = await repo.list_channels()
    usernames = {row["channel_username"] for row in rows}
    assert usernames == {"examplechannel", "otherchannel"}
