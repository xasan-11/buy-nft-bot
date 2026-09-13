from __future__ import annotations

import logging

from telethon import events
from telethon.tl import types

from ..database.repository import OfferStatus, Repository
from ..marketplace.offer_folder import OFFER_FOLDER_NAME, find_offer_folder, remove_peer_id_from_folder

logger = logging.getLogger("offer_decline")


def register_offer_decline_handlers(
    user_client,
    repo: Repository,
    folder_finder=find_offer_folder,
    folder_remover=remove_peer_id_from_folder,
) -> None:
    """Watches for a resale-gift purchase offer being declined or expiring.

    Per core.telegram.org/api/gifts#collectible-gift-purchase-offers: "If
    the offer is declined or if it expires, a messageActionStarGiftPurchaseOfferDeclined
    is emitted, and a refund is issued automatically" — replacing the
    original messageActionStarGiftPurchaseOffer service message in the DM
    with the seller. Whether Telegram delivers that replacement as an edit
    of the same message or as a new one isn't pinned down by the docs, so
    both events.NewMessage and events.MessageEdited are watched here;
    either way the seller's peer is resolved from the update's own chat,
    never re-fetched separately.
    """

    async def _handle(event) -> None:
        action = getattr(event.message, "action", None)
        if not isinstance(action, types.MessageActionStarGiftPurchaseOfferDeclined):
            return

        gift = getattr(action, "gift", None)
        slug = getattr(gift, "slug", None)
        if not slug:
            return  # not a unique collectible offer (shouldn't happen) — nothing to match

        owner_peer_id = await repo.get_offer_owner_peer_id(slug)
        await repo.update_offer_status(slug, OfferStatus.DECLINED)

        if owner_peer_id is None:
            return  # this offer predates the feature or wasn't ours — nothing more to do

        remaining = await repo.count_pending_offers_for_peer(owner_peer_id)
        if remaining > 0:
            return  # seller still has other active offers — stays in the folder

        try:
            folder = await folder_finder(user_client, OFFER_FOLDER_NAME)
            if folder is not None:
                await folder_remover(user_client, folder, owner_peer_id)
        except Exception:
            logger.exception("Failed to remove seller from the '%s' chat folder", OFFER_FOLDER_NAME)

    @user_client.on(events.NewMessage())
    async def _on_new_message(event):
        await _handle(event)

    @user_client.on(events.MessageEdited())
    async def _on_edited_message(event):
        await _handle(event)
