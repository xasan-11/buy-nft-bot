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
    "👤➕ Admin qo'sh": "addadmin",
    "👤➖ Admin o'chir": "removeadmin",
    "👥 Adminlar": "admins",
    "⭐ Stars": "stars_balance",
    "⭐ Take stars": "take_stars",
    # ---- Offer-tashlash: every logged-in user has access to these, not
    # just the owner — see OFFER_ACTIONS below.
    "🔑 Kirish (Login)": "login",
    "💸 Offer narxi": "setofferprice",
    "🏷 Offer darajasi": "setofferlevel",
    "🔢 Offer NFT soni": "setoffernftcount",
    "⏰ Offer muddati": "setofferexpiry",
    "🎯 Offer boshlash": "start_offer",
    "🛑 Offer to'xtatish": "stop_offer",
}

# Available to ANY user (owner included) — the entire "offer-tashlash"
# surface plus connecting their own personal account. Everything else in
# BUTTON_ACTIONS is owner-only (see OWNER_ONLY_ACTIONS below), including
# channel/market monitoring and admin management, which is unchanged from
# before multi-tenancy.
OFFER_ACTIONS = {
    "login", "setofferprice", "setofferlevel", "setoffernftcount",
    "setofferexpiry", "start_offer", "stop_offer",
}

# Everything that isn't in OFFER_ACTIONS is owner-only — channel/market
# monitoring, channel list management, /setmaxprice, /status, and admin
# management (/addadmin, /removeadmin, /admins) are all owner-exclusive
# under the multi-tenant model: being listed as an "admin" no longer grants
# any of this.
OWNER_ONLY_ACTIONS = set(BUTTON_ACTIONS.values()) - OFFER_ACTIONS

_OWNER_LABEL_ORDER = [
    label for label, action in BUTTON_ACTIONS.items() if action in OWNER_ONLY_ACTIONS
]
_USER_LABEL_ORDER = [
    label for label, action in BUTTON_ACTIONS.items() if action in OFFER_ACTIONS
]

# 2 buttons per row, in the order given, so the panel reads top-to-bottom
# as related pairs rather than a cramped grid.
def _rows(labels):
    return [labels[i:i + 2] for i in range(0, len(labels), 2)]


def owner_keyboard():
    """Full menu — every command, exactly as before multi-tenancy — shown
    only to the owner."""
    labels = _OWNER_LABEL_ORDER + _USER_LABEL_ORDER
    return [[Button.text(label, resize=True) for label in row] for row in _rows(labels)]


def user_keyboard():
    """Offer-tashlash-only menu — shown to every non-owner user, admins
    included: connect their own account and manage their own offer run,
    nothing else."""
    return [[Button.text(label, resize=True) for label in row] for row in _rows(_USER_LABEL_ORDER)]


def keyboard_for(is_owner: bool):
    return owner_keyboard() if is_owner else user_keyboard()
