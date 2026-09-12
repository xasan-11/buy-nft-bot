from __future__ import annotations

import logging

from telethon import events

from ..database.repository import Repository
from ..monitoring.channel_monitor import ChannelMonitor, MonitorState
from ..notifications.notifier import Notifier
from . import menu
from .authorization import UNAUTHORIZED_MESSAGE

logger = logging.getLogger("bot")


def parse_positive_int(raw) -> "int | None":
    """Returns the parsed value for a valid positive-integer command
    argument, or None if raw is missing/blank/non-numeric/zero-or-negative.
    Pulled out as a pure function so the /setmaxprice validation itself is
    unit-testable without simulating Telethon's event dispatch."""
    if not raw or not raw.isdigit():
        return None
    value = int(raw)
    return value if value > 0 else None


def register_bot_handlers(
    bot_client,
    repo: Repository,
    monitor: ChannelMonitor,
    state: MonitorState,
    notifier: Notifier,
    market_state: MonitorState,
) -> None:
    """Registers every admin command on the control bot. Every handler
    checks authorization by numeric Telegram user ID against the admins
    table before doing anything else — usernames are never trusted."""

    async def require_admin(event) -> bool:
        if await repo.is_admin(event.sender_id):
            return True
        await event.respond(UNAUTHORIZED_MESSAGE)
        return False

    async def require_owner(event) -> bool:
        if await repo.is_owner(event.sender_id):
            return True
        await event.respond(UNAUTHORIZED_MESSAGE)
        return False

    @bot_client.on(events.NewMessage(pattern=r"^/start$"))
    async def _start(event):
        if await repo.is_admin(event.sender_id):
            await event.respond(
                "🤖 NFT monitoring control bot.\n"
                "Use /status for current state, /sstart to start channel monitoring, "
                "/stop to stop it, /startg and /stopg for market monitoring."
            )
            # By now the bot definitely has this user's entity cached (they
            # just messaged it), so this is the one place the full admin
            # menu is guaranteed to apply successfully.
            await menu.sync_admin_menu(bot_client, event.sender_id)
        else:
            await event.respond(UNAUTHORIZED_MESSAGE)

    @bot_client.on(events.NewMessage(pattern=r"^/addadmin\s+(\d+)$"))
    async def _addadmin(event):
        if not await require_owner(event):
            return
        user_id = int(event.pattern_match.group(1))
        added = await repo.add_admin(user_id, event.sender_id)
        if added:
            await event.respond(f"✅ Added {user_id} as administrator.")
            await menu.sync_admin_menu(bot_client, user_id)
        else:
            await event.respond(f"ℹ️ {user_id} is already an administrator.")

    @bot_client.on(events.NewMessage(pattern=r"^/removeadmin\s+(\d+)$"))
    async def _removeadmin(event):
        if not await require_owner(event):
            return
        user_id = int(event.pattern_match.group(1))
        result = await repo.remove_admin(user_id)
        if result == "owner":
            await event.respond("⛔ The owner cannot be removed.")
        elif result == "not_found":
            await event.respond(f"ℹ️ {user_id} is not an administrator.")
        else:
            await event.respond(f"✅ Removed {user_id} from administrators.")
            await menu.reset_admin_menu(bot_client, user_id)

    @bot_client.on(events.NewMessage(pattern=r"^/admins$"))
    async def _admins(event):
        if not await require_admin(event):
            return
        rows = await repo.list_admins()
        lines = [f"{'👑' if r['is_owner'] else '🛡'} {r['user_id']}" for r in rows]
        await event.respond("Administrators:\n" + "\n".join(lines))

    @bot_client.on(events.NewMessage(pattern=r"^/add\s+@?(\w+)$"))
    async def _add_channel(event):
        if not await require_admin(event):
            return
        username = event.pattern_match.group(1)
        try:
            entity = await monitor.user_client.get_entity(username)
        except Exception as e:
            await event.respond(f"❌ Could not resolve channel @{username}: {e}")
            return
        added = await repo.add_channel(entity.id, getattr(entity, "username", username), event.sender_id)
        await monitor.refresh_channels()
        if added:
            await event.respond(f"✅ Now monitoring @{username}.")
        else:
            await event.respond(f"ℹ️ @{username} is already monitored.")

    @bot_client.on(events.NewMessage(pattern=r"^/remove\s+@?(\w+)$"))
    async def _remove_channel(event):
        if not await require_admin(event):
            return
        username = event.pattern_match.group(1)
        try:
            entity = await monitor.user_client.get_entity(username)
        except Exception as e:
            await event.respond(f"❌ Could not resolve channel @{username}: {e}")
            return
        removed = await repo.remove_channel(entity.id)
        await monitor.refresh_channels()
        if removed:
            await event.respond(f"✅ Stopped monitoring @{username}.")
        else:
            await event.respond(f"ℹ️ @{username} was not monitored.")

    @bot_client.on(events.NewMessage(pattern=r"^/list$"))
    async def _list_channels(event):
        if not await require_admin(event):
            return
        rows = await repo.list_channels()
        if not rows:
            await event.respond("No channels are currently monitored.")
            return
        lines = [f"@{r['channel_username']} ({r['channel_id']})" for r in rows]
        await event.respond("Monitored channels:\n" + "\n".join(lines))

    @bot_client.on(events.NewMessage(pattern=r"^/stop$"))
    async def _stop(event):
        if not await require_admin(event):
            return
        await state.stop()
        await event.respond("🛑 NFT monitoring and automatic purchasing stopped.")
        await notifier.notify_monitoring_stopped()

    @bot_client.on(events.NewMessage(pattern=r"^/sstart$"))
    async def _sstart(event):
        if not await require_admin(event):
            return
        await state.start()
        await monitor.refresh_channels()
        await event.respond("🟢 NFT monitoring and automatic purchasing started.")
        await notifier.notify_monitoring_started()

    @bot_client.on(events.NewMessage(pattern=r"^/startg$"))
    async def _startg(event):
        if not await require_admin(event):
            return
        await market_state.start()
        await event.respond("🟢 Market (resale gift) monitoring and automatic purchasing started.")
        await notifier.notify_market_monitoring_started()

    @bot_client.on(events.NewMessage(pattern=r"^/stopg$"))
    async def _stopg(event):
        if not await require_admin(event):
            return
        await market_state.stop()
        await event.respond("🛑 Market (resale gift) monitoring and automatic purchasing stopped.")
        await notifier.notify_market_monitoring_stopped()

    @bot_client.on(events.NewMessage(pattern=r"^/startpo$"))
    async def _startpo(event):
        if not await require_admin(event):
            return
        await repo.set_verbose_poll_log(True)
        await event.respond(
            "🔍 Verbose polling log yoqildi — market-monitoring har tekshiruv siklida "
            "topgan har bir listing haqida xabar yuboradi (xarid/xato xabarlariga qo'shimcha)."
        )

    @bot_client.on(events.NewMessage(pattern=r"^/stopo$"))
    async def _stopo(event):
        if not await require_admin(event):
            return
        await repo.set_verbose_poll_log(False)
        await event.respond(
            "🔕 Verbose polling log o'chirildi — endi faqat xarid/xato xabarlari davom etadi."
        )

    @bot_client.on(events.NewMessage(pattern=r"^/setmaxprice(?:\s+(\S+))?$"))
    async def _setmaxprice(event):
        if not await require_admin(event):
            return
        value = parse_positive_int(event.pattern_match.group(1))
        if value is None:
            await event.respond("⚠️ Usage: /setmaxprice <positive number of Stars>, e.g. /setmaxprice 300")
            return
        await repo.set_max_price(value)
        logger.info("Max NFT price set to %s by user %s", value, event.sender_id)
        await event.respond(
            f"✅ Maximum NFT price set to {value} ⭐. Applies to both channel and market monitoring."
        )

    @bot_client.on(events.NewMessage(pattern=r"^/status$"))
    async def _status(event):
        if not await require_admin(event):
            return
        stats = await repo.get_stats()
        max_price = await repo.get_max_price()
        channel_running = "RUNNING" if state.is_running() else "STOPPED"
        market_running = "RUNNING" if market_state.is_running() else "STOPPED"
        await event.respond(
            "📊 Status\n"
            f"Channel monitoring: {channel_running}\n"
            f"Market monitoring: {market_running}\n"
            f"Maximum NFT price: {max_price} ⭐\n"
            f"Monitored channels: {stats['channels']}\n"
            f"Total detected NFTs: {stats['total_detected']} "
            f"(channel: {stats['channel_detected']}, market: {stats['market_detected']})\n"
            f"Eligible NFTs: {stats['eligible']}\n"
            f"Purchase attempts: {stats['attempts']}\n"
            f"Successful purchases: {stats['success']}\n"
            f"Failed purchases: {stats['failed']}"
        )
