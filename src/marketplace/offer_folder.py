from __future__ import annotations

import logging
from typing import Optional

from telethon import utils
from telethon.errors import RPCError
from telethon.tl import functions, types

logger = logging.getLogger("offer_folder")

OFFER_FOLDER_NAME = "Offer"

_FILTER_FIELDS = (
    "pinned_peers", "exclude_peers", "contacts", "non_contacts", "groups",
    "broadcasts", "bots", "exclude_muted", "exclude_read", "exclude_archived",
    "title_noanimate", "emoticon", "color",
)


async def find_offer_folder(client, name: str = OFFER_FOLDER_NAME) -> Optional[types.DialogFilter]:
    """Looks up an existing Chat Folder by title via messages.getDialogFilters
    (core.telegram.org/method/messages.getDialogFilters) — the official,
    documented way to enumerate a user's folders. Matching is
    case-insensitive, per spec.

    Only a plain types.DialogFilter is ever matched — messages.DialogFilters
    can also contain DialogFilterDefault (the built-in "All Chats" entry,
    which has no title/include_peers at all) and DialogFilterChatlist
    (a shared folder, edited through different, chatlist-specific methods),
    neither of which this feature is meant to touch.
    """
    try:
        result = await client(functions.messages.GetDialogFiltersRequest())
    except RPCError:
        logger.exception("Failed to fetch dialog filters")
        return None

    target = name.strip().lower()
    for f in getattr(result, "filters", None) or []:
        if not isinstance(f, types.DialogFilter):
            continue
        title = getattr(getattr(f, "title", None), "text", None)
        if title and title.strip().lower() == target:
            return f
    return None


def _peer_key(peer) -> Optional[int]:
    try:
        return utils.get_peer_id(peer)
    except (TypeError, ValueError):
        return None


def _replace_include_peers(folder: types.DialogFilter, include_peers: list) -> types.DialogFilter:
    """messages.updateDialogFilter replaces the *entire* filter object, so
    every other field must be resent completely unchanged — only
    include_peers differs between the folder we read and the one we send
    back (core.telegram.org/method/messages.updateDialogFilter)."""
    return types.DialogFilter(
        id=folder.id,
        title=folder.title,
        include_peers=include_peers,
        **{name: getattr(folder, name) for name in _FILTER_FIELDS},
    )


async def add_peer_to_folder(client, folder: types.DialogFilter, peer) -> bool:
    """Adds `peer` to folder.include_peers. Returns True on success —
    including when the peer was already a member, which is a no-op, not an
    error — and False if the update itself failed. Never raises: folder
    bookkeeping must never abort the offer-sending pipeline.
    """
    try:
        input_peer = await client.get_input_entity(peer)
    except (ValueError, TypeError):
        logger.exception("Could not resolve seller peer to add to the Offer folder")
        return False

    peer_key = _peer_key(input_peer)
    if any(_peer_key(p) == peer_key for p in folder.include_peers):
        return True  # already a member

    updated = _replace_include_peers(folder, [*folder.include_peers, input_peer])
    try:
        await client(functions.messages.UpdateDialogFilterRequest(id=folder.id, filter=updated))
        return True
    except RPCError:
        logger.exception("Failed to add seller to the Offer folder")
        return False


async def remove_peer_id_from_folder(client, folder: types.DialogFilter, peer_id: int) -> bool:
    """Removes whichever entry in folder.include_peers resolves to
    `peer_id` (a marked id, e.g. from telethon.utils.get_peer_id or an
    event's .chat_id). Returns True on success — including when the peer
    wasn't a member, a no-op — and False if the update itself failed.
    """
    remaining = [p for p in folder.include_peers if _peer_key(p) != peer_id]
    if len(remaining) == len(folder.include_peers):
        return True  # wasn't a member

    updated = _replace_include_peers(folder, remaining)
    try:
        await client(functions.messages.UpdateDialogFilterRequest(id=folder.id, filter=updated))
        return True
    except RPCError:
        logger.exception("Failed to remove seller from the Offer folder")
        return False
