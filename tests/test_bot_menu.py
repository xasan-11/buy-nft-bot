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
