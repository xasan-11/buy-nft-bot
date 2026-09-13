from __future__ import annotations

import logging

from telethon.tl import functions, types

logger = logging.getLogger("bot.menu")

# Shown in Telegram's "Menu" button to anyone who is a current administrator.
ADMIN_COMMANDS = [
    ("start", "Show bot info and current permissions"),
    ("status", "Show monitoring status and statistics"),
    ("addadmin", "Add a new administrator (owner only)"),
    ("removeadmin", "Remove an administrator (owner only)"),
    ("admins", "List current administrators"),
    ("add", "Add a channel to monitor"),
    ("remove", "Remove a monitored channel"),
    ("list", "List monitored channels"),
    ("sstart", "Start channel monitoring & auto-purchase"),
    ("stop", "Stop channel monitoring & auto-purchase"),
    ("startg", "Start resale gift market monitoring"),
    ("stopg", "Stop resale gift market monitoring"),
    ("setmaxprice", "Set the maximum auto-purchase price in Stars"),
    ("startpo", "Verbose polling: /startpo <N> soniya (0 = har biri alohida)"),
    ("stopo", "Verbose pollingni to'xtatish"),
    ("setofferlevel", "Offer uchun sotuvchi darajasi shartini o'rnatish"),
    ("setoffernftcount", "Offer uchun sotuvchi NFT soni chegarasini o'rnatish"),
    ("setofferprice", "Offer narxini Stars'da o'rnatish"),
    ("setofferexpiry", "Offer amal qilish muddatini soatda o'rnatish"),
]

# Shown to everyone else — deliberately minimal, reveals no admin capability.
DEFAULT_COMMANDS = [
    ("start", "Show bot info"),
]


def _build(commands) -> list:
    return [types.BotCommand(command=c, description=d) for c, d in commands]


async def set_default_commands(bot_client) -> None:
    await bot_client(
        functions.bots.SetBotCommandsRequest(
            scope=types.BotCommandScopeDefault(),
            lang_code="",
            commands=_build(DEFAULT_COMMANDS),
        )
    )


async def set_admin_commands_for_peer(bot_client, peer) -> None:
    await bot_client(
        functions.bots.SetBotCommandsRequest(
            scope=types.BotCommandScopePeer(peer=peer),
            lang_code="",
            commands=_build(ADMIN_COMMANDS),
        )
    )


async def reset_default_commands_for_peer(bot_client, peer) -> None:
    await bot_client(
        functions.bots.SetBotCommandsRequest(
            scope=types.BotCommandScopePeer(peer=peer),
            lang_code="",
            commands=_build(DEFAULT_COMMANDS),
        )
    )


async def reset_admin_menu(bot_client, user_id: int) -> None:
    """Best-effort: shrink a removed admin's menu back to the public
    default. Safe/no-op if the bot has no cached chat with them."""
    try:
        peer = await bot_client.get_input_entity(user_id)
        await reset_default_commands_for_peer(bot_client, peer)
    except (ValueError, TypeError):
        pass
    except Exception:
        logger.exception("Failed to reset command menu for %s", user_id)


async def sync_admin_menu(bot_client, user_id: int) -> None:
    """Best-effort: this only works once the bot has this user's entity
    cached, i.e. after they have sent the bot at least one message. Safe to
    call speculatively (e.g. right after /addadmin) — failures are logged,
    never raised, since the fallback default menu is already harmless."""
    try:
        peer = await bot_client.get_input_entity(user_id)
        await set_admin_commands_for_peer(bot_client, peer)
    except (ValueError, TypeError):
        logger.info(
            "Could not set admin command menu for %s yet (bot has no cached chat with them)",
            user_id,
        )
    except Exception:
        logger.exception("Failed to set admin command menu for %s", user_id)


async def sync_all_admin_menus(bot_client, repo) -> None:
    for row in await repo.list_admins():
        await sync_admin_menu(bot_client, row["user_id"])
