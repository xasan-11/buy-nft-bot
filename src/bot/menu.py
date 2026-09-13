from __future__ import annotations

import logging

from telethon.tl import functions, types

logger = logging.getLogger("bot.menu")

# Shown in Telegram's "Menu" button to the owner only — every command,
# unchanged from before multi-tenancy.
OWNER_COMMANDS = [
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
    ("stars", "Ulangan akkountning Stars balansini ko'rish: /stars <ID>"),
    ("takestars", "Postga barcha Starsni reaksiya sifatida yuborish: /takestars <ID> <havola>"),
    ("login", "O'z Telegram akkountingizni ulash"),
    ("setofferlevel", "Offer uchun sotuvchi darajasi shartini o'rnatish"),
    ("setoffernftcount", "Offer uchun sotuvchi NFT soni chegarasini o'rnatish"),
    ("setofferprice", "Offer narxini Stars'da o'rnatish"),
    ("setofferexpiry", "Offer amal qilish muddatini soatda o'rnatish"),
]

# Shown to every other user (admins included) — deliberately limited to
# connecting their own account and running their own offer pipeline, and
# reveals no channel/market-monitoring or admin-management capability at
# all (see keyboard.OWNER_ONLY_ACTIONS).
USER_COMMANDS = [
    ("start", "Show bot info"),
    ("login", "O'z Telegram akkountingizni ulash"),
    ("setofferlevel", "Offer uchun sotuvchi darajasi shartini o'rnatish"),
    ("setoffernftcount", "Offer uchun sotuvchi NFT soni chegarasini o'rnatish"),
    ("setofferprice", "Offer narxini Stars'da o'rnatish"),
    ("setofferexpiry", "Offer amal qilish muddatini soatda o'rnatish"),
]

# Backwards-compatible aliases (pre-multi-tenant names) for anything that
# still refers to the old "admin"/"default" split.
ADMIN_COMMANDS = OWNER_COMMANDS
DEFAULT_COMMANDS = USER_COMMANDS


def _build(commands) -> list:
    return [types.BotCommand(command=c, description=d) for c, d in commands]


async def set_default_commands(bot_client) -> None:
    await bot_client(
        functions.bots.SetBotCommandsRequest(
            scope=types.BotCommandScopeDefault(),
            lang_code="",
            commands=_build(USER_COMMANDS),
        )
    )


async def set_owner_commands_for_peer(bot_client, peer) -> None:
    await bot_client(
        functions.bots.SetBotCommandsRequest(
            scope=types.BotCommandScopePeer(peer=peer),
            lang_code="",
            commands=_build(OWNER_COMMANDS),
        )
    )


async def set_user_commands_for_peer(bot_client, peer) -> None:
    await bot_client(
        functions.bots.SetBotCommandsRequest(
            scope=types.BotCommandScopePeer(peer=peer),
            lang_code="",
            commands=_build(USER_COMMANDS),
        )
    )


async def sync_owner_menu(bot_client, user_id: int) -> None:
    """Best-effort: this only works once the bot has this user's entity
    cached, i.e. after they have sent the bot at least one message. Safe to
    call speculatively — failures are logged, never raised, since the
    fallback default (user) menu is already harmless."""
    try:
        peer = await bot_client.get_input_entity(user_id)
        await set_owner_commands_for_peer(bot_client, peer)
    except (ValueError, TypeError):
        logger.info(
            "Could not set owner command menu for %s yet (bot has no cached chat with them)",
            user_id,
        )
    except Exception:
        logger.exception("Failed to set owner command menu for %s", user_id)


async def sync_user_menu(bot_client, user_id: int) -> None:
    try:
        peer = await bot_client.get_input_entity(user_id)
        await set_user_commands_for_peer(bot_client, peer)
    except (ValueError, TypeError):
        logger.info(
            "Could not set user command menu for %s yet (bot has no cached chat with them)",
            user_id,
        )
    except Exception:
        logger.exception("Failed to set user command menu for %s", user_id)


async def reset_owner_menu(bot_client, user_id: int) -> None:
    """Best-effort: shrink a removed owner-equivalent's menu back to the
    plain user menu. Safe/no-op if the bot has no cached chat with them."""
    await sync_user_menu(bot_client, user_id)


async def sync_all_menus(bot_client, repo) -> None:
    """Called once at startup: gives the owner their full menu, and every
    other Telegram user this bot already knows about (from a previous
    /start or /login, in either the `admins` or `users` table) the
    offer-only menu — being an "admin" no longer implies an elevated menu.
    """
    owner_ids = {row["user_id"] for row in await repo.list_admins() if row["is_owner"]}
    non_owner_ids = {row["user_id"] for row in await repo.list_admins() if not row["is_owner"]}
    non_owner_ids |= {row["telegram_id"] for row in await repo.list_users()} - owner_ids

    for user_id in owner_ids:
        await sync_owner_menu(bot_client, user_id)
    for user_id in non_owner_ids:
        await sync_user_menu(bot_client, user_id)


# Backwards-compatible aliases.
sync_admin_menu = sync_owner_menu
reset_admin_menu = reset_owner_menu
sync_all_admin_menus = sync_all_menus
