from __future__ import annotations

from telethon import Button

# Human-readable label -> internal action key. Each key has a matching
# do_<action> function in bot_app.py that holds the *actual* command logic
# (shared with the plain-text /command handlers) — tapping a button never
# duplicates behavior, it only drives the same code path with a friendlier
# label. Keys ending in no particular suffix run immediately; ones listed in
# bot_app.PARAMETERIZED_ACTIONS prompt for a value first.
BUTTON_ACTIONS = {
    "📊 Holat": "status",
    "🟢 Kanal boshlash": "sstart",
    "🔴 Kanal to'xtatish": "stop",
    "🛒 Market boshlash": "startg",
    "⏹ Market to'xtatish": "stopg",
    "🔍 Kuzatuv yoqish": "startpo",
    "🔇 Kuzatuv o'chirish": "stopo",
    "➕ Kanal qo'sh": "add",
    "➖ Kanal o'chir": "remove",
    "📋 Kanallar ro'yxati": "list",
    "💰 Max narx": "setmaxprice",
    "💸 Offer narxi": "setofferprice",
    "🏷 Offer darajasi": "setofferlevel",
    "🔢 Offer NFT soni": "setoffernftcount",
    "⏰ Offer muddati": "setofferexpiry",
    "🎯 Offer boshlash": "start_offer",
    "🛑 Offer to'xtatish": "stop_offer",
    "👤➕ Admin qo'sh": "addadmin",
    "👤➖ Admin o'chir": "removeadmin",
    "👥 Adminlar": "admins",
}

# Owner-only actions — same restriction as the underlying /addadmin and
# /removeadmin commands already enforce; listed here only so the button
# tap handler can ask before prompting for a value.
OWNER_ONLY_ACTIONS = {"addadmin", "removeadmin"}

# 2 buttons per row, in the order given, so the panel reads top-to-bottom
# as related pairs rather than a cramped grid.
_LABEL_ORDER = list(BUTTON_ACTIONS.keys())
ADMIN_KEYBOARD_ROWS = [_LABEL_ORDER[i:i + 2] for i in range(0, len(_LABEL_ORDER), 2)]


def admin_keyboard():
    return [[Button.text(label, resize=True) for label in row] for row in ADMIN_KEYBOARD_ROWS]
