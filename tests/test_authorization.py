from tests.conftest import ADMIN_ID, OWNER_ID, STRANGER_ID


async def test_owner_has_full_access(repo):
    assert await repo.is_admin(OWNER_ID)
    assert await repo.is_owner(OWNER_ID)


async def test_stranger_is_not_authorized(repo):
    assert not await repo.is_admin(STRANGER_ID)


async def test_owner_can_add_admin(repo):
    added = await repo.add_admin(ADMIN_ID, added_by=OWNER_ID)
    assert added
    assert await repo.is_admin(ADMIN_ID)
    assert not await repo.is_owner(ADMIN_ID)


async def test_adding_existing_admin_is_noop(repo):
    await repo.add_admin(ADMIN_ID, added_by=OWNER_ID)
    added_again = await repo.add_admin(ADMIN_ID, added_by=OWNER_ID)
    assert not added_again


async def test_removed_admin_loses_access(repo):
    await repo.add_admin(ADMIN_ID, added_by=OWNER_ID)
    result = await repo.remove_admin(ADMIN_ID)
    assert result == "removed"
    assert not await repo.is_admin(ADMIN_ID)


async def test_owner_cannot_be_removed(repo):
    result = await repo.remove_admin(OWNER_ID)
    assert result == "owner"
    assert await repo.is_admin(OWNER_ID)
    assert await repo.is_owner(OWNER_ID)


async def test_removing_unknown_admin_reports_not_found(repo):
    result = await repo.remove_admin(STRANGER_ID)
    assert result == "not_found"


async def test_admins_list_contains_owner(repo):
    await repo.add_admin(ADMIN_ID, added_by=OWNER_ID)
    rows = await repo.list_admins()
    ids = {row["user_id"] for row in rows}
    assert OWNER_ID in ids
    assert ADMIN_ID in ids
