from __future__ import annotations

from telethon.errors import RPCError
from telethon.tl import functions, types

from src.marketplace.offer_folder import (
    add_peer_to_folder,
    find_offer_folder,
    remove_peer_id_from_folder,
)


def make_dialog_filter(id=1, title="Offer", include_peers=None):
    """A real telethon.tl.types.DialogFilter — offer_folder.py's isinstance
    check needs the genuine type, not a duck-typed stand-in, to correctly
    tell it apart from DialogFilterDefault/DialogFilterChatlist."""
    return types.DialogFilter(
        id=id,
        title=types.TextWithEntities(text=title, entities=[]),
        pinned_peers=[],
        include_peers=include_peers if include_peers is not None else [],
        exclude_peers=[],
    )


class FakeFiltersResult:
    def __init__(self, filters):
        self.filters = filters


class FakeFolderClient:
    def __init__(self, filters_result=None, update_error=None, resolve_error=None):
        self.filters_result = filters_result
        self.update_error = update_error
        self.resolve_error = resolve_error
        self.update_requests = []

    async def get_input_entity(self, peer):
        if self.resolve_error:
            raise self.resolve_error
        return peer  # tests pass already-"resolved" fakes

    async def __call__(self, request):
        if isinstance(request, functions.messages.GetDialogFiltersRequest):
            return self.filters_result
        if isinstance(request, functions.messages.UpdateDialogFilterRequest):
            self.update_requests.append(request)
            if self.update_error:
                raise self.update_error
            return True
        raise AssertionError(f"unexpected request {request}")


async def test_find_offer_folder_matches_by_title_case_insensitively():
    folder = make_dialog_filter(title="oFFeR")
    client = FakeFolderClient(filters_result=FakeFiltersResult([folder]))

    found = await find_offer_folder(client, "Offer")

    assert found is folder


async def test_find_offer_folder_skips_default_and_unrelated_filters():
    target = make_dialog_filter(id=2, title="Offer")
    client = FakeFolderClient(
        filters_result=FakeFiltersResult([
            types.DialogFilterDefault(),
            make_dialog_filter(id=1, title="Work"),
            target,
        ])
    )

    found = await find_offer_folder(client, "Offer")

    assert found is target


async def test_find_offer_folder_returns_none_when_missing():
    client = FakeFolderClient(filters_result=FakeFiltersResult([make_dialog_filter(title="Work")]))

    found = await find_offer_folder(client, "Offer")

    assert found is None


async def test_find_offer_folder_returns_none_on_rpc_error():
    client = FakeFolderClient()
    client.filters_result = None

    async def raising_call(request):
        raise RPCError(None, "SOME_ERROR")

    client.__call__ = raising_call

    found = await find_offer_folder(client, "Offer")

    assert found is None


async def test_add_peer_to_folder_appends_and_resends_whole_filter():
    folder = make_dialog_filter(include_peers=[types.InputPeerUser(user_id=1, access_hash=1)])
    client = FakeFolderClient()
    new_peer = types.InputPeerUser(user_id=2, access_hash=2)

    result = await add_peer_to_folder(client, folder, new_peer)

    assert result is True
    [request] = client.update_requests
    assert request.id == folder.id
    assert len(request.filter.include_peers) == 2
    assert request.filter.title is folder.title  # every other field resent unchanged
    assert request.filter.exclude_peers is folder.exclude_peers


async def test_add_peer_to_folder_is_a_noop_when_already_present():
    peer = types.InputPeerUser(user_id=5, access_hash=5)
    folder = make_dialog_filter(include_peers=[peer])
    client = FakeFolderClient()

    result = await add_peer_to_folder(client, folder, peer)

    assert result is True
    assert client.update_requests == []  # no update sent — already a member


async def test_add_peer_to_folder_matches_by_normalized_peer_id_not_object_identity():
    """A freshly resolved InputPeerUser(5, ...) must be recognized as "the
    same peer" as an existing entry with the same user_id, even though
    they're different Python objects (and even different access_hash)."""
    existing = types.InputPeerUser(user_id=5, access_hash=999)
    folder = make_dialog_filter(include_peers=[existing])
    client = FakeFolderClient()
    same_user_new_object = types.InputPeerUser(user_id=5, access_hash=111)

    result = await add_peer_to_folder(client, folder, same_user_new_object)

    assert result is True
    assert client.update_requests == []


async def test_add_peer_to_folder_returns_false_on_rpc_error():
    folder = make_dialog_filter()
    client = FakeFolderClient(update_error=RPCError(None, "SOME_ERROR"))

    result = await add_peer_to_folder(client, folder, types.InputPeerUser(user_id=1, access_hash=1))

    assert result is False


async def test_add_peer_to_folder_returns_false_when_peer_cannot_be_resolved():
    folder = make_dialog_filter()
    client = FakeFolderClient(resolve_error=ValueError("cannot resolve"))

    result = await add_peer_to_folder(client, folder, types.InputPeerUser(user_id=1, access_hash=1))

    assert result is False
    assert client.update_requests == []


async def test_remove_peer_id_from_folder_removes_matching_entry():
    peer_to_remove = types.InputPeerUser(user_id=7, access_hash=7)
    keep = types.InputPeerUser(user_id=8, access_hash=8)
    folder = make_dialog_filter(include_peers=[peer_to_remove, keep])
    client = FakeFolderClient()

    result = await remove_peer_id_from_folder(client, folder, peer_id=7)

    assert result is True
    [request] = client.update_requests
    assert len(request.filter.include_peers) == 1
    assert request.filter.include_peers[0] is keep


async def test_remove_peer_id_from_folder_is_a_noop_when_not_present():
    folder = make_dialog_filter(include_peers=[types.InputPeerUser(user_id=8, access_hash=8)])
    client = FakeFolderClient()

    result = await remove_peer_id_from_folder(client, folder, peer_id=999)

    assert result is True
    assert client.update_requests == []


async def test_remove_peer_id_from_folder_returns_false_on_rpc_error():
    folder = make_dialog_filter(include_peers=[types.InputPeerUser(user_id=7, access_hash=7)])
    client = FakeFolderClient(update_error=RPCError(None, "SOME_ERROR"))

    result = await remove_peer_id_from_folder(client, folder, peer_id=7)

    assert result is False
