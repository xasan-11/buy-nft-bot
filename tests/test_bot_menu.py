from src.bot import menu


def test_admin_commands_cover_all_new_and_existing_commands():
    names = {c for c, _ in menu.ADMIN_COMMANDS}
    expected = {
        "start", "status", "addadmin", "removeadmin", "admins",
        "add", "remove", "list", "sstart", "stop", "startg", "stopg",
        "setmaxprice", "startpo", "stopo",
    }
    assert expected.issubset(names)


def test_default_commands_do_not_leak_admin_capability():
    names = {c for c, _ in menu.DEFAULT_COMMANDS}
    assert "setmaxprice" not in names
    assert "addadmin" not in names


class FakeBotClientNoEntity:
    async def get_input_entity(self, user_id):
        raise ValueError("Cannot find any entity")


async def test_sync_admin_menu_swallows_unknown_peer():
    # Should not raise even though the bot has never seen this user yet.
    await menu.sync_admin_menu(FakeBotClientNoEntity(), 999999)


async def test_reset_admin_menu_swallows_unknown_peer():
    await menu.reset_admin_menu(FakeBotClientNoEntity(), 999999)


def test_user_commands_expose_only_login_and_offer_settings():
    names = {c for c, _ in menu.USER_COMMANDS}
    assert names == {
        "start", "login", "setofferlevel", "setoffernftcount",
        "setofferprice", "setofferexpiry",
    }


def test_user_commands_never_leak_owner_capability():
    names = {c for c, _ in menu.USER_COMMANDS}
    for owner_only in ("status", "sstart", "stop", "startg", "stopg", "add", "remove", "list", "setmaxprice", "addadmin", "removeadmin", "admins"):
        assert owner_only not in names


async def test_sync_all_menus_gives_owner_the_full_menu_and_admin_the_user_menu(repo):
    calls = []

    class RecordingBotClient:
        async def get_input_entity(self, user_id):
            return user_id

        async def __call__(self, request):
            calls.append((request.scope.peer, {c.command for c in request.commands}))

    await repo.add_admin(222, added_by=111)
    await menu.sync_all_menus(RecordingBotClient(), repo)

    by_peer = dict(calls)
    assert by_peer[111] == {c for c, _ in menu.OWNER_COMMANDS}
    assert by_peer[222] == {c for c, _ in menu.USER_COMMANDS}
