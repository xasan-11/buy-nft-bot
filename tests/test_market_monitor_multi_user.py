from __future__ import annotations

from src.marketplace.offer import OfferOutcome
from src.marketplace.seller_profile import SellerProfile
from src.monitoring.channel_monitor import MonitorState
from src.monitoring.market_monitor import MarketMonitor
from src.monitoring.offer_registry import OfferRegistry

OWNER_ID = 111
OTHER_ID = 222
THIRD_ID = 333


class FakeUniqueGift:
    def __init__(self, slug, price=999, offer_min_stars=None, owner_id="owner", title=None):
        self.slug = slug
        self.resell_amount = [type("Amt", (), {"amount": price})()]
        self.resale_ton_only = False
        self.offer_min_stars = offer_min_stars
        self.owner_id = owner_id
        self.title = title


class FakeNotifier:
    def __init__(self):
        self.offers_sent = []
        self.offers_failed = []

    async def notify_purchased(self, *a, **k):
        pass

    async def notify_ignored(self, *a, **k):
        pass

    async def notify_failed(self, *a, **k):
        pass

    async def notify_offer_sent(self, name, price, duration_hours, nft_link=None, seller_profile_link=None):
        self.offers_sent.append((name, price))

    async def notify_offer_failed(self, name, price, reason):
        self.offers_failed.append((name, price, reason))

    async def notify_offer_paused(self):
        pass

    async def notify_offer_resumed(self, balance, required):
        pass

    async def notify_offer_folder_missing(self, folder_name):
        pass


class FakeClient:
    """Distinct identity per user — only used so assertions can tell which
    user's client an offer/profile call actually went through."""

    def __init__(self, owner_id):
        self.owner_id = owner_id

    def __repr__(self):
        return f"FakeClient({self.owner_id})"


class FakeSessionManager:
    def __init__(self, clients):
        self._clients = clients

    def is_ready(self, telegram_id):
        return telegram_id in self._clients

    def get_client(self, telegram_id):
        return self._clients.get(telegram_id)


def make_profile_fetcher(level=1, gift_count=0):
    async def fetcher(client, owner_peer):
        return SellerProfile(level=level, gift_count=gift_count)
    return fetcher


def make_offer_executor(calls):
    async def executor(client, owner_peer, slug, price, duration_seconds):
        calls.append((client, slug, price))
        return OfferOutcome(success=True, offer_id=1)
    return executor


async def make_multi_user_monitor(repo, active_user_ids, gift_id=1):
    notifier = FakeNotifier()
    state = MonitorState(repo)
    await state.load()
    await state.start()

    clients = {uid: FakeClient(uid) for uid in active_user_ids}
    session_manager = FakeSessionManager(clients)
    offer_registry = OfferRegistry(repo)

    for uid in active_user_ids:
        offer_state = await offer_registry.get(uid)
        await offer_state.start()
        await repo.set_user_offer_selected_gift_types(uid, {gift_id})

    calls = []
    monitor = MarketMonitor(
        user_client=object(),  # the shared scanning account — never used for offer sends here
        repo=repo,
        state=state,
        notifier=notifier,
        request_spacing_seconds=0,
        offer_registry=offer_registry,
        session_manager=session_manager,
        offer_executor=make_offer_executor(calls),
        seller_profile_fetcher=make_profile_fetcher(),
    )
    return monitor, notifier, offer_registry, calls


async def test_two_active_users_both_independently_receive_an_offer(repo):
    monitor, notifier, offer_registry, calls = await make_multi_user_monitor(
        repo, active_user_ids=[OWNER_ID, OTHER_ID]
    )

    await monitor.handle_candidate(
        gift_id=1, unique=FakeUniqueGift("Gift-Shared", offer_min_stars=50, title="Shared")
    )

    # Both users sent their own offer through their own client, for the
    # very same listing — offering doesn't reserve the gift like buying does.
    assert len(calls) == 2
    used_clients = {c.owner_id for c, slug, price in calls}
    assert used_clients == {OWNER_ID, OTHER_ID}
    assert len(notifier.offers_sent) == 2

    stats = await repo.get_offer_stats()
    assert stats["pending"] == 2  # one row per user for the same slug


async def test_inactive_user_is_skipped_while_active_user_still_gets_an_offer(repo):
    monitor, notifier, offer_registry, calls = await make_multi_user_monitor(
        repo, active_user_ids=[OWNER_ID]
    )
    # THIRD_ID never started a run at all — never even touched the registry.

    await monitor.handle_candidate(
        gift_id=1, unique=FakeUniqueGift("Gift-Solo", offer_min_stars=50, title="Solo")
    )

    assert len(calls) == 1
    assert calls[0][0].owner_id == OWNER_ID


async def test_paused_user_does_not_receive_an_offer(repo):
    monitor, notifier, offer_registry, calls = await make_multi_user_monitor(
        repo, active_user_ids=[OWNER_ID, OTHER_ID]
    )
    paused_state = await offer_registry.get(OTHER_ID)
    await paused_state.pause()

    await monitor.handle_candidate(
        gift_id=1, unique=FakeUniqueGift("Gift-Half", offer_min_stars=50, title="Half")
    )

    assert len(calls) == 1
    assert calls[0][0].owner_id == OWNER_ID


async def test_each_users_price_and_level_filter_applies_independently(repo):
    monitor, notifier, offer_registry, calls = await make_multi_user_monitor(
        repo, active_user_ids=[OWNER_ID, OTHER_ID]
    )
    await repo.set_user_offer_price(OWNER_ID, 200)
    await repo.set_user_offer_price(OTHER_ID, 40)  # below offer_min_stars=50 -> skipped

    await monitor.handle_candidate(
        gift_id=1, unique=FakeUniqueGift("Gift-Price", offer_min_stars=50, title="Price")
    )

    assert len(calls) == 1
    assert calls[0][0].owner_id == OWNER_ID
    assert calls[0][2] == 200


async def test_user_not_logged_in_is_skipped_even_if_their_run_is_active(repo):
    """An OfferState can be active (e.g. restored from a previous process)
    while that user's TelegramClient isn't connected/ready yet — must not
    be treated as a valid offer target until session_manager says so."""
    notifier = FakeNotifier()
    state = MonitorState(repo)
    await state.load()
    await state.start()

    offer_registry = OfferRegistry(repo)
    offer_state = await offer_registry.get(OTHER_ID)
    await offer_state.start()
    await repo.set_user_offer_selected_gift_types(OTHER_ID, {1})

    session_manager = FakeSessionManager(clients={})  # nobody is actually logged in
    calls = []
    monitor = MarketMonitor(
        user_client=object(),
        repo=repo,
        state=state,
        notifier=notifier,
        request_spacing_seconds=0,
        offer_registry=offer_registry,
        session_manager=session_manager,
        offer_executor=make_offer_executor(calls),
        seller_profile_fetcher=make_profile_fetcher(),
    )

    await monitor.handle_candidate(
        gift_id=1, unique=FakeUniqueGift("Gift-NotLoggedIn", offer_min_stars=50)
    )

    assert calls == []
