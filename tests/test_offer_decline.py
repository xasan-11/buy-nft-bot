from __future__ import annotations

from telethon import events
from telethon.tl import types

from src.database.repository import OfferStatus
from src.telegram.offer_decline import register_offer_decline_handlers

USER_ID = 111


class FakeStarGiftUnique:
    def __init__(self, slug):
        self.slug = slug


class FakeAction:
    """Any object at all works for a non-matching action — only isinstance
    against types.MessageActionStarGiftPurchaseOfferDeclined matters."""


class FakeMessage:
    def __init__(self, action):
        self.action = action


class FakeEvent:
    def __init__(self, action, chat_id=999):
        self.message = FakeMessage(action)
        self.chat_id = chat_id


class FakeClient:
    """Records every @user_client.on(...) registration; register_offer_decline_handlers
    only ever registers unconditional (no-pattern) NewMessage/MessageEdited
    builders, so dispatch here is just "call every handler with the event"."""

    def __init__(self):
        self.handlers = []

    def on(self, builder):
        def decorator(callback):
            self.handlers.append((builder, callback))
            return callback
        return decorator

    async def dispatch_new_message(self, event):
        # MessageEdited is a NewMessage subclass in Telethon, so exact-type
        # matching (not isinstance) is required to tell them apart here.
        for builder, callback in self.handlers:
            if type(builder) is events.NewMessage:
                await callback(event)

    async def dispatch_edited_message(self, event):
        for builder, callback in self.handlers:
            if type(builder) is events.MessageEdited:
                await callback(event)


def make_declined_action(slug, expired=False):
    return types.MessageActionStarGiftPurchaseOfferDeclined(
        gift=FakeStarGiftUnique(slug), price=types.StarsAmount(amount=125, nanos=0), expired=expired
    )


def make_folder_finder(folder=None):
    async def finder(client, name):
        return folder
    return finder


def make_folder_remover(calls=None, result=True):
    async def remover(client, folder, peer_id):
        if calls is not None:
            calls.append((folder, peer_id))
        return result
    return remover


async def test_ignores_unrelated_message_actions(repo):
    await repo.claim_offer_slot(
        USER_ID, offer_id=1, slug="Gift-1", gift_id=1, price_stars=125, duration_seconds=21600,
        expires_at="2999-01-01T00:00:00+00:00", owner_peer_id=42,
    )
    client = FakeClient()
    register_offer_decline_handlers(client, repo, USER_ID)

    await client.dispatch_new_message(FakeEvent(FakeAction()))

    stats = await repo.get_offer_stats()
    assert stats["pending"] == 1  # untouched


async def test_declined_action_updates_status_and_removes_from_folder_when_no_other_pending(repo):
    await repo.claim_offer_slot(
        USER_ID, offer_id=1, slug="Gift-2", gift_id=1, price_stars=125, duration_seconds=21600,
        expires_at="2999-01-01T00:00:00+00:00", owner_peer_id=42,
    )
    folder = object()
    remove_calls = []
    client = FakeClient()
    register_offer_decline_handlers(
        client, repo, USER_ID,
        folder_finder=make_folder_finder(folder),
        folder_remover=make_folder_remover(remove_calls),
    )

    await client.dispatch_new_message(FakeEvent(make_declined_action("Gift-2"), chat_id=42))

    stats = await repo.get_offer_stats()
    assert stats["declined"] == 1
    assert remove_calls == [(folder, 42)]


async def test_declined_action_also_detected_via_message_edit(repo):
    """Telegram's docs don't pin down whether the transition is an edit of
    the original offer message or a brand new one — both event types must
    be watched."""
    await repo.claim_offer_slot(
        USER_ID, offer_id=1, slug="Gift-3", gift_id=1, price_stars=125, duration_seconds=21600,
        expires_at="2999-01-01T00:00:00+00:00", owner_peer_id=42,
    )
    remove_calls = []
    client = FakeClient()
    register_offer_decline_handlers(
        client, repo, USER_ID,
        folder_finder=make_folder_finder(object()),
        folder_remover=make_folder_remover(remove_calls),
    )

    await client.dispatch_edited_message(FakeEvent(make_declined_action("Gift-3"), chat_id=42))

    stats = await repo.get_offer_stats()
    assert stats["declined"] == 1
    assert len(remove_calls) == 1


async def test_seller_kept_in_folder_while_another_offer_is_still_pending(repo):
    await repo.claim_offer_slot(
        USER_ID, offer_id=1, slug="Gift-4a", gift_id=1, price_stars=125, duration_seconds=21600,
        expires_at="2999-01-01T00:00:00+00:00", owner_peer_id=77,
    )
    await repo.claim_offer_slot(
        USER_ID, offer_id=2, slug="Gift-4b", gift_id=1, price_stars=125, duration_seconds=21600,
        expires_at="2999-01-01T00:00:00+00:00", owner_peer_id=77,  # same seller, still pending
    )
    remove_calls = []
    client = FakeClient()
    register_offer_decline_handlers(
        client, repo, USER_ID,
        folder_finder=make_folder_finder(object()),
        folder_remover=make_folder_remover(remove_calls),
    )

    await client.dispatch_new_message(FakeEvent(make_declined_action("Gift-4a"), chat_id=77))

    stats = await repo.get_offer_stats()
    assert stats["declined"] == 1
    assert stats["pending"] == 1  # Gift-4b
    assert remove_calls == []  # seller still has an active offer -> stays in the folder


async def test_seller_removed_once_their_last_pending_offer_also_resolves(repo):
    await repo.claim_offer_slot(
        USER_ID, offer_id=1, slug="Gift-5a", gift_id=1, price_stars=125, duration_seconds=21600,
        expires_at="2999-01-01T00:00:00+00:00", owner_peer_id=77,
    )
    await repo.claim_offer_slot(
        USER_ID, offer_id=2, slug="Gift-5b", gift_id=1, price_stars=125, duration_seconds=21600,
        expires_at="2999-01-01T00:00:00+00:00", owner_peer_id=77,
    )
    remove_calls = []
    client = FakeClient()
    register_offer_decline_handlers(
        client, repo, USER_ID,
        folder_finder=make_folder_finder(object()),
        folder_remover=make_folder_remover(remove_calls),
    )

    await client.dispatch_new_message(FakeEvent(make_declined_action("Gift-5a"), chat_id=77))
    assert remove_calls == []  # Gift-5b still pending

    await client.dispatch_new_message(FakeEvent(make_declined_action("Gift-5b"), chat_id=77))
    assert len(remove_calls) == 1  # now removed, once every pending offer resolved


async def test_untracked_offer_updates_status_but_skips_folder_lookup_gracefully(repo):
    """A decline for a slug this bot never recorded (e.g. pre-dates this
    feature) must not crash — update_offer_status is a no-op UPDATE with no
    matching row, and there is no owner_peer_id to act on."""
    remove_calls = []
    client = FakeClient()
    register_offer_decline_handlers(
        client, repo, USER_ID,
        folder_finder=make_folder_finder(object()),
        folder_remover=make_folder_remover(remove_calls),
    )

    await client.dispatch_new_message(FakeEvent(make_declined_action("Never-Seen-Slug"), chat_id=1))

    assert remove_calls == []


async def test_folder_not_found_does_not_crash(repo):
    await repo.claim_offer_slot(
        USER_ID, offer_id=1, slug="Gift-6", gift_id=1, price_stars=125, duration_seconds=21600,
        expires_at="2999-01-01T00:00:00+00:00", owner_peer_id=42,
    )
    client = FakeClient()
    register_offer_decline_handlers(client, repo, USER_ID, folder_finder=make_folder_finder(None))

    await client.dispatch_new_message(FakeEvent(make_declined_action("Gift-6"), chat_id=42))

    stats = await repo.get_offer_stats()
    assert stats["declined"] == 1  # status still updated even though the folder step no-oped


async def test_folder_lookup_exception_does_not_crash(repo):
    await repo.claim_offer_slot(
        USER_ID, offer_id=1, slug="Gift-7", gift_id=1, price_stars=125, duration_seconds=21600,
        expires_at="2999-01-01T00:00:00+00:00", owner_peer_id=42,
    )

    async def raising_finder(client, name):
        raise RuntimeError("boom")

    client = FakeClient()
    register_offer_decline_handlers(client, repo, USER_ID, folder_finder=raising_finder)

    await client.dispatch_new_message(FakeEvent(make_declined_action("Gift-7"), chat_id=42))

    stats = await repo.get_offer_stats()
    assert stats["declined"] == 1
