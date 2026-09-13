from __future__ import annotations

import logging
import re

from telethon import Button, events
from telethon.tl import functions

from ..database.repository import ALLOWED_OFFER_HOURS, Repository
from ..monitoring.channel_monitor import ChannelMonitor, MonitorState
from ..monitoring.offer_state import OfferState
from ..notifications.notifier import Notifier
from . import gift_picker, keyboard, menu
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


def parse_nonnegative_int(raw) -> "int | None":
    """Like parse_positive_int, but 0 is a valid value (used by /startpo,
    where 0 means "immediate, unaggregated" mode rather than an interval)."""
    if not raw or not raw.isdigit():
        return None
    return int(raw)


def parse_offer_hours(raw) -> "int | None":
    """/setofferexpiry only accepts one of ALLOWED_OFFER_HOURS — Telegram's
    payments.sendStarGiftOffer rejects any other duration outright."""
    value = parse_positive_int(raw)
    if value is None or value not in ALLOWED_OFFER_HOURS:
        return None
    return value


def parse_channel_username(raw) -> "str | None":
    """Same shape /add and /remove's regex already required (optional
    leading @, then word characters only) — extracted so the reply-keyboard
    flow (which gets free-form text, not a regex capture group) validates
    identically to typing the command out."""
    if not raw:
        return None
    match = re.fullmatch(r"@?(\w+)", raw.strip())
    return match.group(1) if match else None


def parse_user_id(raw) -> "int | None":
    if not raw:
        return None
    raw = raw.strip()
    return int(raw) if raw.isdigit() else None


# Button labels that require a follow-up value message before they can run
# (see keyboard.BUTTON_ACTIONS) — every other action key runs immediately.
PARAMETERIZED_ACTIONS = {
    "add", "remove", "setmaxprice", "setofferprice", "setofferlevel",
    "setoffernftcount", "setofferexpiry", "addadmin", "removeadmin",
    "startpo",
}
# "start_offer" is intentionally not here — it now opens the inline
# gift-type picker (see gift_picker.py) instead of prompting for a plain
# text value, so it's driven entirely by CallbackQuery handlers below.


def register_bot_handlers(
    bot_client,
    repo: Repository,
    monitor: ChannelMonitor,
    state: MonitorState,
    notifier: Notifier,
    market_state: MonitorState,
    offer_state: OfferState,
) -> None:
    """Registers every admin command on the control bot. Every handler
    checks authorization by numeric Telegram user ID against the admins
    table before doing anything else — usernames are never trusted.

    Each command's actual behavior lives in one do_<action> function below,
    called both by the plain-text /command regex handler (which already has
    its argument from the regex capture group) and by the persistent
    reply-keyboard flow (button tap -> prompt -> value reply) further down.
    Tapping a button never re-implements a command, it only drives the same
    function through a friendlier two-step conversation.
    """

    # sender_id -> action key awaiting a follow-up value message, set only
    # when a parameterized button (see PARAMETERIZED_ACTIONS) is tapped. A
    # plain-text /command never touches this — it already carries its
    # argument inline via the regex.
    pending_action: dict = {}

    # sender_id -> in-progress gift-type picker state ("🎯 Offer boshlash"),
    # keyed the same way — see start_gift_picker/render_gift_page below.
    # Ephemeral/in-memory like pending_action: a restart mid-pick just means
    # the admin taps the button again.
    gift_sessions: dict = {}

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

    # ------------------------------------------------------ core commands
    # (identical bodies to the original single-purpose handlers — only the
    # argument now arrives as a plain parameter instead of always being
    # read straight from event.pattern_match)

    async def do_addadmin(event, raw) -> "bool | None":
        if not await require_owner(event):
            return
        user_id = parse_user_id(raw)
        if user_id is None:
            await event.respond("⚠️ Foydalanuvchining raqamli Telegram ID'sini kiriting.")
            return False
        added = await repo.add_admin(user_id, event.sender_id)
        if added:
            await event.respond(f"✅ Added {user_id} as administrator.")
            await menu.sync_admin_menu(bot_client, user_id)
        else:
            await event.respond(f"ℹ️ {user_id} is already an administrator.")

    async def do_removeadmin(event, raw) -> "bool | None":
        if not await require_owner(event):
            return
        user_id = parse_user_id(raw)
        if user_id is None:
            await event.respond("⚠️ Foydalanuvchining raqamli Telegram ID'sini kiriting.")
            return False
        result = await repo.remove_admin(user_id)
        if result == "owner":
            await event.respond("⛔ The owner cannot be removed.")
        elif result == "not_found":
            await event.respond(f"ℹ️ {user_id} is not an administrator.")
        else:
            await event.respond(f"✅ Removed {user_id} from administrators.")
            await menu.reset_admin_menu(bot_client, user_id)

    async def do_admins(event) -> None:
        if not await require_admin(event):
            return
        rows = await repo.list_admins()
        lines = [f"{'👑' if r['is_owner'] else '🛡'} {r['user_id']}" for r in rows]
        await event.respond("Administrators:\n" + "\n".join(lines))

    async def do_add_channel(event, raw) -> "bool | None":
        if not await require_admin(event):
            return
        username = parse_channel_username(raw)
        if username is None:
            await event.respond("⚠️ Kanal username'ini kiriting, masalan @kanal.")
            return False
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

    async def do_remove_channel(event, raw) -> "bool | None":
        if not await require_admin(event):
            return
        username = parse_channel_username(raw)
        if username is None:
            await event.respond("⚠️ Kanal username'ini kiriting, masalan @kanal.")
            return False
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

    async def do_list(event) -> None:
        if not await require_admin(event):
            return
        rows = await repo.list_channels()
        if not rows:
            await event.respond("No channels are currently monitored.")
            return
        lines = [f"@{r['channel_username']} ({r['channel_id']})" for r in rows]
        await event.respond("Monitored channels:\n" + "\n".join(lines))

    async def do_stop(event) -> None:
        if not await require_admin(event):
            return
        await state.stop()
        await event.respond("🛑 NFT monitoring and automatic purchasing stopped.")
        await notifier.notify_monitoring_stopped()

    async def do_sstart(event) -> None:
        if not await require_admin(event):
            return
        await state.start()
        await monitor.refresh_channels()
        await event.respond("🟢 NFT monitoring and automatic purchasing started.")
        await notifier.notify_monitoring_started()

    async def do_startg(event) -> None:
        if not await require_admin(event):
            return
        await market_state.start()
        await event.respond("🟢 Market (resale gift) monitoring and automatic purchasing started.")
        await notifier.notify_market_monitoring_started()

    async def do_stopg(event) -> None:
        if not await require_admin(event):
            return
        await market_state.stop()
        await event.respond("🛑 Market (resale gift) monitoring and automatic purchasing stopped.")
        await notifier.notify_market_monitoring_stopped()

    async def do_startpo(event, raw) -> "bool | None":
        if not await require_admin(event):
            return
        seconds = parse_nonnegative_int(raw)
        if seconds is None:
            await event.respond("❌ Noto'g'ri qiymat, N musbat son yoki 0 bo'lishi kerak")
            return False
        await repo.set_verbose_poll_log(True)
        await repo.set_verbose_poll_interval(seconds)
        if seconds == 0:
            await event.respond(
                "🔍 Verbose polling yoqildi — har bir topilgan listing aniqlangan zahoti "
                "alohida-alohida xabar qilinadi (xarid/xato xabarlariga qo'shimcha)."
            )
        else:
            await event.respond(
                f"🔍 Verbose polling yoqildi — har {seconds} soniyada shu vaqt ichida topilgan "
                "barcha yangi listinglar bitta umumlashtirilgan xabarda yuboriladi "
                "(xarid/xato xabarlariga qo'shimcha)."
            )

    async def do_stopo(event) -> None:
        if not await require_admin(event):
            return
        await repo.set_verbose_poll_log(False)
        await event.respond(
            "🔕 Verbose polling log o'chirildi — endi faqat xarid/xato xabarlari davom etadi."
        )

    async def do_setmaxprice(event, raw) -> "bool | None":
        if not await require_admin(event):
            return
        value = parse_positive_int(raw)
        if value is None:
            await event.respond("⚠️ Usage: /setmaxprice <positive number of Stars>, e.g. /setmaxprice 300")
            return False
        await repo.set_max_price(value)
        logger.info("Max NFT price set to %s by user %s", value, event.sender_id)
        await event.respond(
            f"✅ Maximum NFT price set to {value} ⭐. Applies to both channel and market monitoring."
        )

    async def do_setofferlevel(event, raw) -> "bool | None":
        if not await require_admin(event):
            return
        value = parse_nonnegative_int(raw)
        if value is None:
            await event.respond("⚠️ Usage: /setofferlevel <0 yoki musbat son>, masalan /setofferlevel 1")
            return False
        await repo.set_offer_level(value)
        await event.respond(f"✅ Offer uchun sotuvchi darajasi shartini {value} ga o'rnatdim.")

    async def do_setoffernftcount(event, raw) -> "bool | None":
        if not await require_admin(event):
            return
        value = parse_positive_int(raw)
        if value is None:
            await event.respond("⚠️ Usage: /setoffernftcount <musbat son>, masalan /setoffernftcount 3")
            return False
        await repo.set_offer_nft_count(value)
        await event.respond(
            f"✅ Offer faqat {value} tadan kam NFT/gift egasi bo'lgan sotuvchilarga tashlanadi."
        )

    async def do_setofferprice(event, raw) -> "bool | None":
        if not await require_admin(event):
            return
        value = parse_positive_int(raw)
        if value is None:
            await event.respond("⚠️ Usage: /setofferprice <musbat Stars soni>, masalan /setofferprice 125")
            return False
        await repo.set_offer_price(value)
        await event.respond(f"✅ Offer narxi {value} ⭐ ga o'rnatildi.")

    async def do_setofferexpiry(event, raw) -> "bool | None":
        if not await require_admin(event):
            return
        value = parse_offer_hours(raw)
        if value is None:
            allowed = ", ".join(str(h) for h in ALLOWED_OFFER_HOURS)
            await event.respond(
                f"⚠️ Telegram faqat quyidagi muddatlarni (soatda) qabul qiladi: {allowed}.\n"
                "Masalan: /setofferexpiry 6"
            )
            return False
        await repo.set_offer_expiry_hours(value)
        await event.respond(f"✅ Offer amal qilish muddati {value} soatga o'rnatildi.")

    def build_gift_keyboard(session):
        """One row per gift type on the current page — a plain text toggle
        button (its own emoji if the sticker carried one, else 🎁 + name +
        price, ✅/⬜ marking selection) — no media involved at all, plus a
        pagination row (when there's more than one page) and a Start row.
        Shared by render_gift_page (initial send / page change) and
        _gift_toggle (re-editing the same message in place after a tap)."""
        page_items = gift_picker.paginate(session["options"], session["page"])
        pages = gift_picker.total_pages(len(session["options"]))

        rows = [
            [Button.inline(
                gift_picker.gift_button_text(
                    option.emoji, option.title, option.stars, option.id in session["selected"]
                ),
                data=f"giftsel:{option.id}",
            )]
            for option in page_items
        ]

        nav_row = []
        if session["page"] > 0:
            nav_row.append(Button.inline("◀️ Oldingi", data="giftpage:prev"))
        if session["page"] < pages - 1:
            nav_row.append(Button.inline("Keyingi ▶️", data="giftpage:next"))
        if nav_row:
            rows.append(nav_row)
        rows.append([Button.inline("▶️ Start", data="giftstart")])

        return rows, pages

    async def render_gift_page(event, sender_id) -> None:
        """(Re)draws the current page of the gift-type picker for one admin
        as a single plain-text message with an inline keyboard — no photos
        or documents are sent (that was hitting DocumentInvalidError). Any
        message from a previously rendered page for this same sender is
        deleted first, so paging never leaves stale duplicates behind."""
        session = gift_sessions[sender_id]
        if session["message_ids"]:
            try:
                await bot_client.delete_messages(event.chat_id, session["message_ids"])
            except Exception:
                logger.exception("Failed to clear previous gift-picker page for %s", sender_id)
            session["message_ids"] = []

        buttons, pages = build_gift_keyboard(session)
        msg = await event.respond(
            gift_picker.page_caption(session["page"], pages, len(session["selected"])),
            buttons=buttons,
        )
        session["message_ids"] = [msg.id]

    async def start_gift_picker(event) -> None:
        if not await require_admin(event):
            return

        stale = gift_sessions.pop(event.sender_id, None)
        if stale and stale["message_ids"]:
            try:
                await bot_client.delete_messages(event.chat_id, stale["message_ids"])
            except Exception:
                logger.exception("Failed to clear stale gift-picker session for %s", event.sender_id)

        try:
            result = await monitor.user_client(functions.payments.GetStarGiftsRequest(hash=0))
        except Exception:
            logger.exception("Failed to fetch gift types for the offer picker")
            await event.respond("❌ Gift turlarini olishda xatolik yuz berdi. Birozdan so'ng qayta urinib ko'ring.")
            return

        options = gift_picker.resalable_gift_options(getattr(result, "gifts", None) or [])
        if not options:
            await event.respond("ℹ️ Hozircha resale uchun mavjud gift turi topilmadi.")
            return

        already_selected = await repo.get_offer_selected_gift_types()
        available_ids = {o.id for o in options}
        gift_sessions[event.sender_id] = {
            "options": options,
            "selected": already_selected & available_ids,
            "page": 0,
            "message_ids": [],
        }
        await render_gift_page(event, event.sender_id)

    async def do_stop_offer(event) -> None:
        if not await require_admin(event):
            return
        sent = offer_state.sent
        await offer_state.stop()
        await event.respond(f"🛑 Offer tashlash to'xtatildi. Jami {sent} ta offer tashlandi.")

    async def do_status(event) -> None:
        if not await require_admin(event):
            return
        stats = await repo.get_stats()
        offer_stats = await repo.get_offer_stats()
        max_price = await repo.get_max_price()
        channel_running = "RUNNING" if state.is_running() else "STOPPED"
        market_running = "RUNNING" if market_state.is_running() else "STOPPED"
        offer_level = await repo.get_offer_level()
        offer_nft_count = await repo.get_offer_nft_count()
        offer_price = await repo.get_offer_price()
        offer_expiry_hours = await repo.get_offer_expiry_hours()
        selected_gift_types = await repo.get_offer_selected_gift_types()
        if not offer_state.is_active():
            offer_run_line = "Offer run: STOPPED"
        elif offer_state.is_paused():
            offer_run_line = (
                f"Offer run: PAUSED ⏸ (balans yetarli emas — {offer_state.sent} ta yuborilgan edi, "
                f"{len(selected_gift_types)} ta gift turi tanlangan)"
            )
        else:
            offer_run_line = (
                f"Offer run: ACTIVE ({offer_state.sent} ta yuborildi, "
                f"{len(selected_gift_types)} ta gift turi tanlangan)"
            )
        verbose_on = await repo.get_verbose_poll_log()
        if not verbose_on:
            verbose_line = "Verbose polling: OFF"
        else:
            interval = await repo.get_verbose_poll_interval()
            verbose_line = (
                "Verbose polling: ON (har bir listing alohida)"
                if interval == 0
                else f"Verbose polling: ON (interval: {interval}s)"
            )
        await event.respond(
            "📊 Status\n"
            f"Channel monitoring: {channel_running}\n"
            f"Market monitoring: {market_running}\n"
            f"{verbose_line}\n"
            f"Maximum NFT price: {max_price} ⭐\n"
            f"Monitored channels: {stats['channels']}\n"
            f"Total detected NFTs: {stats['total_detected']} "
            f"(channel: {stats['channel_detected']}, market: {stats['market_detected']})\n"
            f"Eligible NFTs: {stats['eligible']}\n"
            f"Purchase attempts: {stats['attempts']}\n"
            f"Successful purchases: {stats['success']}\n"
            f"Failed purchases: {stats['failed']}\n"
            "\n"
            "🎯 Offer sozlamalari\n"
            f"Sotuvchi darajasi (=): {offer_level}\n"
            f"Sotuvchi NFT soni (<): {offer_nft_count}\n"
            f"Offer narxi: {offer_price} ⭐\n"
            f"Offer muddati: {offer_expiry_hours} soat\n"
            f"{offer_run_line}\n"
            f"Offerlar: {offer_stats['total']} jami "
            f"(pending: {offer_stats['pending']}, accepted: {offer_stats['accepted']}, "
            f"declined: {offer_stats['declined']}, expired: {offer_stats['expired']}, "
            f"failed: {offer_stats['failed']})"
        )

    # ---------------------------------------------------- plain-text /commands

    @bot_client.on(events.NewMessage(pattern=r"^/start$"))
    async def _start(event):
        if await repo.is_admin(event.sender_id):
            await event.respond(
                "🤖 NFT monitoring control bot.\n"
                "Use /status for current state, /sstart to start channel monitoring, "
                "/stop to stop it, /startg and /stopg for market monitoring.",
                buttons=keyboard.admin_keyboard(),
            )
            # By now the bot definitely has this user's entity cached (they
            # just messaged it), so this is the one place the full admin
            # menu is guaranteed to apply successfully.
            await menu.sync_admin_menu(bot_client, event.sender_id)
        else:
            await event.respond(UNAUTHORIZED_MESSAGE, buttons=Button.clear())

    @bot_client.on(events.NewMessage(pattern=r"^/addadmin\s+(\d+)$"))
    async def _addadmin(event):
        await do_addadmin(event, event.pattern_match.group(1))

    @bot_client.on(events.NewMessage(pattern=r"^/removeadmin\s+(\d+)$"))
    async def _removeadmin(event):
        await do_removeadmin(event, event.pattern_match.group(1))

    @bot_client.on(events.NewMessage(pattern=r"^/admins$"))
    async def _admins(event):
        await do_admins(event)

    @bot_client.on(events.NewMessage(pattern=r"^/add\s+@?(\w+)$"))
    async def _add_channel(event):
        await do_add_channel(event, event.pattern_match.group(1))

    @bot_client.on(events.NewMessage(pattern=r"^/remove\s+@?(\w+)$"))
    async def _remove_channel(event):
        await do_remove_channel(event, event.pattern_match.group(1))

    @bot_client.on(events.NewMessage(pattern=r"^/list$"))
    async def _list_channels(event):
        await do_list(event)

    @bot_client.on(events.NewMessage(pattern=r"^/stop$"))
    async def _stop(event):
        await do_stop(event)

    @bot_client.on(events.NewMessage(pattern=r"^/sstart$"))
    async def _sstart(event):
        await do_sstart(event)

    @bot_client.on(events.NewMessage(pattern=r"^/startg$"))
    async def _startg(event):
        await do_startg(event)

    @bot_client.on(events.NewMessage(pattern=r"^/stopg$"))
    async def _stopg(event):
        await do_stopg(event)

    @bot_client.on(events.NewMessage(pattern=r"^/startpo(?:\s+(\S+))?$"))
    async def _startpo(event):
        await do_startpo(event, event.pattern_match.group(1))

    @bot_client.on(events.NewMessage(pattern=r"^/stopo$"))
    async def _stopo(event):
        await do_stopo(event)

    @bot_client.on(events.NewMessage(pattern=r"^/setmaxprice(?:\s+(\S+))?$"))
    async def _setmaxprice(event):
        await do_setmaxprice(event, event.pattern_match.group(1))

    @bot_client.on(events.NewMessage(pattern=r"^/setofferlevel(?:\s+(\S+))?$"))
    async def _setofferlevel(event):
        await do_setofferlevel(event, event.pattern_match.group(1))

    @bot_client.on(events.NewMessage(pattern=r"^/setoffernftcount(?:\s+(\S+))?$"))
    async def _setoffernftcount(event):
        await do_setoffernftcount(event, event.pattern_match.group(1))

    @bot_client.on(events.NewMessage(pattern=r"^/setofferprice(?:\s+(\S+))?$"))
    async def _setofferprice(event):
        await do_setofferprice(event, event.pattern_match.group(1))

    @bot_client.on(events.NewMessage(pattern=r"^/setofferexpiry(?:\s+(\S+))?$"))
    async def _setofferexpiry(event):
        await do_setofferexpiry(event, event.pattern_match.group(1))

    @bot_client.on(events.NewMessage(pattern=r"^/status$"))
    async def _status(event):
        await do_status(event)

    # ------------------------------------------------- persistent-keyboard flow

    # Every no-argument button just runs its do_ function immediately.
    # "start_offer" opens the inline gift-type picker rather than running
    # to completion synchronously, but it still takes no *text* argument —
    # the picker itself is driven by the CallbackQuery handlers below.
    no_arg_handlers = {
        "status": do_status,
        "sstart": do_sstart,
        "stop": do_stop,
        "startg": do_startg,
        "stopg": do_stopg,
        "stopo": do_stopo,
        "list": do_list,
        "admins": do_admins,
        "stop_offer": do_stop_offer,
        "start_offer": start_gift_picker,
    }

    # Every parameterized button prompts with this text, then the next
    # message from that same sender is fed into the matching do_ function.
    action_prompts = {
        "add": "➕ Qo'shmoqchi bo'lgan kanal username'ini kiriting (masalan @kanal):",
        "remove": "➖ O'chirmoqchi bo'lgan kanal username'ini kiriting:",
        "setmaxprice": "💰 Yangi maksimal narxni kiriting (Stars, musbat son):",
        "setofferprice": "💸 Yangi offer narxini kiriting (Stars, musbat son):",
        "setofferlevel": "🏷 Offer uchun talab qilinadigan sotuvchi darajasini kiriting (0 yoki musbat son):",
        "setoffernftcount": "🔢 Sotuvchida bo'lishi kerak bo'lgan maksimal NFT sonini kiriting (musbat son):",
        "setofferexpiry": (
            f"⏰ Offer muddatini soatda kiriting ({', '.join(str(h) for h in ALLOWED_OFFER_HOURS)}):"
        ),
        "addadmin": "👤➕ Admin qilib qo'shmoqchi bo'lgan foydalanuvchining raqamli Telegram ID'sini kiriting:",
        "removeadmin": "👤➖ Administratorlikdan olib tashlamoqchi bo'lgan foydalanuvchining ID'sini kiriting:",
        "startpo": "🔍 Necha soniyalik oraliqda kuzatuv xabarlari yuborilsin? (0 = darhol xabar):",
    }

    parameterized_handlers = {
        "add": do_add_channel,
        "remove": do_remove_channel,
        "setmaxprice": do_setmaxprice,
        "setofferprice": do_setofferprice,
        "setofferlevel": do_setofferlevel,
        "setoffernftcount": do_setoffernftcount,
        "setofferexpiry": do_setofferexpiry,
        "addadmin": do_addadmin,
        "removeadmin": do_removeadmin,
        "startpo": do_startpo,
    }

    button_pattern = "^(" + "|".join(re.escape(label) for label in keyboard.BUTTON_ACTIONS) + ")$"

    @bot_client.on(events.NewMessage(pattern=button_pattern))
    async def _button_tap(event):
        action = keyboard.BUTTON_ACTIONS[event.pattern_match.group(1)]

        if action in keyboard.OWNER_ONLY_ACTIONS:
            if not await require_owner(event):
                return
        else:
            if not await require_admin(event):
                return

        # A fresh button tap always supersedes any half-finished flow this
        # same admin left hanging (e.g. tapped "💰 Max narx" then changed
        # their mind and tapped something else instead of replying).
        pending_action.pop(event.sender_id, None)

        if action in no_arg_handlers:
            await no_arg_handlers[action](event)
            return

        pending_action[event.sender_id] = action
        await event.respond(action_prompts[action])

    @bot_client.on(events.NewMessage())
    async def _pending_value_reply(event):
        # A button tap is never a value reply, regardless of whatever might
        # still be pending for this sender — _button_tap handles it
        # independently and this must not also treat the label text as the
        # answer to an older prompt.
        text = (event.raw_text or "").strip()
        if text in keyboard.BUTTON_ACTIONS:
            return

        action = pending_action.get(event.sender_id)
        if action is None:
            return

        keep_waiting = await parameterized_handlers[action](event, text)
        if keep_waiting is not False:
            pending_action.pop(event.sender_id, None)

    # ------------------------------------------------- gift-type picker flow
    # (inline keyboard / CallbackQuery, triggered by start_gift_picker above)

    @bot_client.on(events.CallbackQuery(pattern=r"^giftsel:(\d+)$"))
    async def _gift_toggle(event):
        session = gift_sessions.get(event.sender_id)
        if session is None:
            await event.answer()
            return
        gift_id = int(event.pattern_match.group(1))
        option = next((o for o in session["options"] if o.id == gift_id), None)
        if option is None:
            await event.answer()
            return

        session["selected"] = gift_picker.toggle_selection(session["selected"], gift_id)
        buttons, pages = build_gift_keyboard(session)
        await event.edit(
            gift_picker.page_caption(session["page"], pages, len(session["selected"])),
            buttons=buttons,
        )

    @bot_client.on(events.CallbackQuery(pattern=r"^giftpage:(prev|next)$"))
    async def _gift_page(event):
        session = gift_sessions.get(event.sender_id)
        if session is None:
            await event.answer()
            return

        pages = gift_picker.total_pages(len(session["options"]))
        if event.pattern_match.group(1) == b"prev":
            session["page"] = max(0, session["page"] - 1)
        else:
            session["page"] = min(pages - 1, session["page"] + 1)

        await render_gift_page(event, event.sender_id)
        await event.answer()

    @bot_client.on(events.CallbackQuery(pattern=r"^giftstart$"))
    async def _gift_start(event):
        session = gift_sessions.get(event.sender_id)
        if not session or not session["selected"]:
            await event.answer("❌ Avval kamida bitta NFT tanlang", alert=True)
            return

        selected = session["selected"]
        await repo.set_offer_selected_gift_types(selected)
        await offer_state.start()

        try:
            await bot_client.delete_messages(event.chat_id, session["message_ids"])
        except Exception:
            logger.exception("Failed to clear gift-picker messages for %s", event.sender_id)
        gift_sessions.pop(event.sender_id, None)

        await event.respond(f"▶️ Offer tashlash boshlandi. Tanlangan gift turlari: {len(selected)} ta.")
