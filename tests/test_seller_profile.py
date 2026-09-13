from __future__ import annotations

from telethon.errors import RPCError
from telethon.tl import functions, types

from src.marketplace.seller_profile import get_seller_profile


class FakeStarsRating:
    def __init__(self, level: int):
        self.level = level


class FakeUserFull:
    def __init__(self, level, gift_count, user_id=123):
        self.id = user_id
        self.stars_rating = FakeStarsRating(level) if level is not None else None
        self.stargifts_count = gift_count


class FakeUser:
    def __init__(self, user_id, username=None):
        self.id = user_id
        self.username = username


class FakeUserFullResult:
    def __init__(self, full_user, users=None):
        self.full_user = full_user
        self.users = users if users is not None else [FakeUser(full_user.id)]


class FakeChatFull:
    def __init__(self, gift_count, chat_id=456):
        self.id = chat_id
        self.stargifts_count = gift_count


class FakeChat:
    def __init__(self, chat_id, username=None):
        self.id = chat_id
        self.username = username


class FakeChatFullResult:
    def __init__(self, full_chat, chats=None):
        self.full_chat = full_chat
        self.chats = chats if chats is not None else [FakeChat(full_chat.id)]


class FakeProfileClient:
    def __init__(self, user_result=None, chat_result=None, error=None):
        self.user_result = user_result
        self.chat_result = chat_result
        self.error = error

    async def __call__(self, request):
        if self.error:
            raise self.error
        if isinstance(request, functions.users.GetFullUserRequest):
            return self.user_result
        if isinstance(request, functions.channels.GetFullChannelRequest):
            return self.chat_result
        raise AssertionError(f"unexpected request {request}")


async def test_user_owner_reads_level_and_gift_count():
    client = FakeProfileClient(user_result=FakeUserFullResult(FakeUserFull(level=2, gift_count=1)))

    profile = await get_seller_profile(client, types.PeerUser(user_id=123))

    assert profile.level == 2
    assert profile.gift_count == 1


async def test_user_without_stars_rating_defaults_to_level_zero():
    client = FakeProfileClient(user_result=FakeUserFullResult(FakeUserFull(level=None, gift_count=0)))

    profile = await get_seller_profile(client, types.PeerUser(user_id=123))

    assert profile.level == 0
    assert profile.gift_count == 0


async def test_channel_owner_has_no_level_concept():
    client = FakeProfileClient(chat_result=FakeChatFullResult(FakeChatFull(gift_count=4)))

    profile = await get_seller_profile(client, types.PeerChannel(channel_id=456))

    assert profile.level == 0
    assert profile.gift_count == 4


async def test_rpc_error_fails_closed_returns_none():
    client = FakeProfileClient(error=RPCError(None, "USER_PRIVACY_RESTRICTED"))

    profile = await get_seller_profile(client, types.PeerUser(user_id=123))

    assert profile is None


async def test_user_with_username_builds_t_me_profile_link():
    full_user = FakeUserFull(level=1, gift_count=0, user_id=123)
    client = FakeProfileClient(
        user_result=FakeUserFullResult(full_user, users=[FakeUser(123, username="theseller")])
    )

    profile = await get_seller_profile(client, types.PeerUser(user_id=123))

    assert profile.username == "theseller"
    assert profile.user_id == 123
    assert profile.profile_link == "https://t.me/theseller"


async def test_user_without_username_falls_back_to_tg_deep_link():
    full_user = FakeUserFull(level=1, gift_count=0, user_id=123)
    client = FakeProfileClient(
        user_result=FakeUserFullResult(full_user, users=[FakeUser(123, username=None)])
    )

    profile = await get_seller_profile(client, types.PeerUser(user_id=123))

    assert profile.username is None
    assert profile.profile_link == "tg://user?id=123"


async def test_channel_with_username_builds_t_me_profile_link():
    full_chat = FakeChatFull(gift_count=4, chat_id=456)
    client = FakeProfileClient(
        chat_result=FakeChatFullResult(full_chat, chats=[FakeChat(456, username="mychannel")])
    )

    profile = await get_seller_profile(client, types.PeerChannel(channel_id=456))

    assert profile.username == "mychannel"
    assert profile.profile_link == "https://t.me/mychannel"


async def test_channel_without_username_has_no_profile_link():
    """Unlike a user, a channel has no tg://user?id= equivalent — the
    offer-sent notification simply omits the seller-profile button."""
    full_chat = FakeChatFull(gift_count=4, chat_id=456)
    client = FakeProfileClient(
        chat_result=FakeChatFullResult(full_chat, chats=[FakeChat(456, username=None)])
    )

    profile = await get_seller_profile(client, types.PeerChannel(channel_id=456))

    assert profile.username is None
    assert profile.user_id is None
    assert profile.profile_link is None
