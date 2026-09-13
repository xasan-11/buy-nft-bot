from __future__ import annotations

import asyncio

from src.marketplace.offer import OfferOutcome
from src.marketplace.purchase import PurchaseOutcome
from src.marketplace.seller_profile import SellerProfile
from src.monitoring.channel_monitor import MonitorState
from src.monitoring.market_monitor import MarketMonitor
from src.monitoring.offer_state import OfferState


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
        self.purchased = []
        self.ignored = []
        self.failed = []
        self.offers_sent = []
        self.offers_failed = []
        self.paused_notifications = 0
        self.resumed_notifications = []
        self.offer_folder_missing_count = 0

    async def notify_purchased(self, *a, **k):
        self.purchased.append((a, k))

    async def notify_ignored(self, *a, **k):
        self.ignored.append((a, k))

    async def notify_failed(self, *a, **k):
        self.failed.append((a, k))

    async def notify_offer_sent(self, name, price, duration_hours, nft_link=None, seller_profile_link=None):
        self.offers_sent.append((name, price, duration_hours, nft_link, seller_profile_link))

    async def notify_offer_failed(self, name, price, reason):
        self.offers_failed.append((name, price, reason))

    async def notify_offer_paused(self):
        self.paused_notifications += 1

    async def notify_offer_resumed(self, balance, required):
        self.resumed_notifications.append((balance, required))

    async def notify_offer_folder_missing(self, folder_name):
        self.offer_folder_missing_count += 1


def make_purchase_executor(outcome=None):
    async def executor(client, slug, max_price, is_running):
        return outcome or PurchaseOutcome(success=True, price_stars=100)
    return executor


def make_offer_executor(outcome=None, calls=None):
    async def executor(client, owner_peer, slug, price, duration_seconds):
        if calls is not None:
            calls.append((owner_peer, slug, price, duration_seconds))
        return outcome or OfferOutcome(success=True, offer_id=42)
    return executor


def make_profile_fetcher(profile):
    async def fetcher(client, owner_peer):
        return profile
    return fetcher


def make_balance_fetcher(amount=None):
    async def fetcher(client):
        return amount
    return fetcher


class FakeTitle:
    def __init__(self, text):
        self.text = text


class FakeDialogFilter:
    """Stand-in for telethon.tl.types.DialogFilter — carries only the
    fields offer_folder.py actually reads/rewrites."""

    def __init__(self, id=1, title="Offer", include_peers=None):
        self.id = id
        self.title = FakeTitle(title)
        self.include_peers = include_peers if include_peers is not None else []
        self.pinned_peers = []
        self.exclude_peers = []
        self.contacts = None
        self.non_contacts = None
        self.groups = None
        self.broadcasts = None
        self.bots = None
        self.exclude_muted = None
        self.exclude_read = None
        self.exclude_archived = None
        self.title_noanimate = None
        self.emoticon = None
        self.color = None


def make_folder_finder(folder=None):
    async def finder(client, name):
        return folder
    return finder


def make_folder_adder(calls=None, result=True):
    async def adder(client, folder, peer):
        if calls is not None:
            calls.append((folder, peer))
        return result
    return adder


async def make_monitor(
    repo, offer_active_target=0, profile=None, offer_outcome=None, offer_calls=None,
    selected_gift_types=None, unbounded=False, balance_amount=None,
    offer_folder_finder=None, offer_folder_adder=None,
):
    """`offer_active_target`/`unbounded` are both just "start the offer run
    or not" at this point — OfferState has no target-count concept, every
    run is unbounded. Both names are kept only so existing call sites in
    this file don't all need editing; either truthy value starts it."""
    notifier = FakeNotifier()
    state = MonitorState(repo)
    await state.load()
    await state.start()

    offer_state = OfferState(repo)
    await offer_state.load()
    if offer_active_target or unbounded:
        await offer_state.start()
        # Every test in this module uses gift_id=1 unless it says otherwise
        # via selected_gift_types — default the picker's selection to match
        # so existing assertions don't all need to opt in explicitly.
        await repo.set_offer_selected_gift_types(
            selected_gift_types if selected_gift_types is not None else {1}
        )

    monitor = MarketMonitor(
        user_client=object(),
        repo=repo,
        state=state,
        notifier=notifier,
        purchase_executor=make_purchase_executor(),
        request_spacing_seconds=0,
        offer_state=offer_state,
        offer_executor=make_offer_executor(offer_outcome, offer_calls),
        seller_profile_fetcher=make_profile_fetcher(profile),
        balance_fetcher=make_balance_fetcher(balance_amount),
        offer_folder_finder=offer_folder_finder or make_folder_finder(FakeDialogFilter()),
        offer_folder_adder=offer_folder_adder or make_folder_adder(),
    )
    return monitor, notifier, offer_state


async def test_offer_not_sent_when_offer_run_not_active(repo):
    monitor, notifier, offer_state = await make_monitor(repo, offer_active_target=0)

    await monitor.handle_candidate(
        gift_id=1, unique=FakeUniqueGift("Gift-A", offer_min_stars=50)
    )

    assert notifier.offers_sent == []


async def test_offer_skipped_when_gift_type_not_selected_in_picker(repo):
    """The gift-type picker filter applies on top of, not instead of, every
    other check — a listing whose type wasn't selected is skipped even
    though the seller would otherwise pass level/count."""
    monitor, notifier, offer_state = await make_monitor(
        repo,
        offer_active_target=1,
        profile=SellerProfile(level=1, gift_count=0),
        selected_gift_types={99},  # gift_id=1 below is NOT in this set
    )

    await monitor.handle_candidate(gift_id=1, unique=FakeUniqueGift("Gift-NotPicked", offer_min_stars=50))

    assert notifier.offers_sent == []


async def test_offer_sent_when_gift_type_is_among_several_selected(repo):
    monitor, notifier, offer_state = await make_monitor(
        repo,
        offer_active_target=1,
        profile=SellerProfile(level=1, gift_count=0),
        selected_gift_types={7, 1, 42},
    )

    await monitor.handle_candidate(gift_id=1, unique=FakeUniqueGift("Gift-Picked", offer_min_stars=50))

    assert len(notifier.offers_sent) == 1


async def test_offer_skipped_when_owner_has_not_enabled_offers(repo):
    monitor, notifier, offer_state = await make_monitor(
        repo, offer_active_target=1, profile=SellerProfile(level=1, gift_count=0)
    )

    await monitor.handle_candidate(gift_id=1, unique=FakeUniqueGift("Gift-B", offer_min_stars=None))

    assert notifier.offers_sent == []
    assert offer_state.sent == 0


async def test_offer_skipped_when_configured_price_below_minimum(repo):
    await repo.set_offer_price(50)
    monitor, notifier, offer_state = await make_monitor(
        repo, offer_active_target=1, profile=SellerProfile(level=1, gift_count=0)
    )

    await monitor.handle_candidate(gift_id=1, unique=FakeUniqueGift("Gift-C", offer_min_stars=100))

    assert notifier.offers_sent == []


async def test_offer_skipped_when_seller_level_does_not_match(repo):
    # default required level is 1
    monitor, notifier, offer_state = await make_monitor(
        repo, offer_active_target=1, profile=SellerProfile(level=2, gift_count=0)
    )

    await monitor.handle_candidate(gift_id=1, unique=FakeUniqueGift("Gift-D", offer_min_stars=50))

    assert notifier.offers_sent == []


async def test_offer_skipped_when_seller_owns_too_many_gifts(repo):
    # default max nft count is 3 (must be strictly less than)
    monitor, notifier, offer_state = await make_monitor(
        repo, offer_active_target=1, profile=SellerProfile(level=1, gift_count=3)
    )

    await monitor.handle_candidate(gift_id=1, unique=FakeUniqueGift("Gift-E", offer_min_stars=50))

    assert notifier.offers_sent == []


async def test_offer_sent_when_seller_matches_all_criteria(repo):
    monitor, notifier, offer_state = await make_monitor(
        repo,
        offer_active_target=1,
        profile=SellerProfile(level=1, gift_count=0, username="theseller", user_id=999),
    )

    await monitor.handle_candidate(
        gift_id=1, unique=FakeUniqueGift("Gift-F", offer_min_stars=50, title="Plush Pepe")
    )

    assert len(notifier.offers_sent) == 1
    name, price, hours, nft_link, seller_link = notifier.offers_sent[0]
    assert name == "Plush Pepe"
    assert price == 125  # default offer price
    assert hours == 6  # default offer expiry
    assert nft_link == "https://t.me/nft/Gift-F"
    assert seller_link == "https://t.me/theseller"
    assert offer_state.sent == 1
    assert offer_state.is_active() is True  # unbounded — keeps running until Stop is tapped

    stats = await repo.get_offer_stats()
    assert stats["pending"] == 1


async def test_offer_sent_notification_omits_seller_link_when_channel_has_no_username(repo):
    monitor, notifier, offer_state = await make_monitor(
        repo, offer_active_target=1, profile=SellerProfile(level=1, gift_count=0)
    )

    await monitor.handle_candidate(
        gift_id=1, unique=FakeUniqueGift("Gift-F2", offer_min_stars=50, title="Plush Pepe")
    )

    _, _, _, nft_link, seller_link = notifier.offers_sent[0]
    assert nft_link == "https://t.me/nft/Gift-F2"
    assert seller_link is None


async def test_offer_seller_profile_unresolvable_skips_offer(repo):
    monitor, notifier, offer_state = await make_monitor(repo, offer_active_target=1, profile=None)

    await monitor.handle_candidate(gift_id=1, unique=FakeUniqueGift("Gift-G", offer_min_stars=50))

    assert notifier.offers_sent == []


async def test_offer_failure_is_recorded_and_notified(repo):
    monitor, notifier, offer_state = await make_monitor(
        repo,
        offer_active_target=1,
        profile=SellerProfile(level=1, gift_count=0),
        offer_outcome=OfferOutcome(success=False, offer_id=7, error="RESELL_STARS_TOO_FEW"),
    )

    await monitor.handle_candidate(gift_id=1, unique=FakeUniqueGift("Gift-H", offer_min_stars=50, title="X"))

    assert notifier.offers_sent == []
    assert len(notifier.offers_failed) == 1
    assert offer_state.sent == 0  # a failed send must not count toward the target
    stats = await repo.get_offer_stats()
    assert stats["failed"] == 1


async def test_offer_stopped_mid_flight_before_send_is_aborted(repo):
    """A stop that lands while the seller-profile lookup is in flight must
    still prevent the send — mirrors the purchase pipeline's pre-payment
    re-check."""
    notifier = FakeNotifier()
    state = MonitorState(repo)
    await state.load()
    await state.start()

    offer_state = OfferState(repo)
    await offer_state.load()
    await offer_state.start()

    calls = []

    async def stopping_profile_fetcher(client, owner_peer):
        await offer_state.stop()  # simulate /Stop offer landing mid-lookup
        return SellerProfile(level=1, gift_count=0)

    monitor = MarketMonitor(
        user_client=object(),
        repo=repo,
        state=state,
        notifier=notifier,
        purchase_executor=make_purchase_executor(),
        request_spacing_seconds=0,
        offer_state=offer_state,
        offer_executor=make_offer_executor(calls=calls),
        seller_profile_fetcher=stopping_profile_fetcher,
    )

    await monitor.handle_candidate(gift_id=1, unique=FakeUniqueGift("Gift-I", offer_min_stars=50))

    assert calls == []
    assert notifier.offers_sent == []


# --------------------------------------------------- BALANCE_TOO_LOW pausing

async def test_balance_too_low_pauses_and_sends_exactly_one_notification(repo):
    monitor, notifier, offer_state = await make_monitor(
        repo,
        unbounded=True,
        profile=SellerProfile(level=1, gift_count=0),
        offer_outcome=OfferOutcome(success=False, offer_id=1, error="400: BALANCE_TOO_LOW (caused by ...)"),
    )

    await monitor.handle_candidate(gift_id=1, unique=FakeUniqueGift("Gift-J", offer_min_stars=50))

    assert offer_state.is_paused() is True
    assert notifier.paused_notifications == 1
    assert notifier.offers_failed == []  # no "Offer tashlanmadi" for a balance pause
    stats = await repo.get_offer_stats()
    assert stats["failed"] == 1  # still recorded in the DB, just not broadcast


async def test_second_listing_after_pause_is_fully_skipped(repo):
    """The actual regression: a queue of otherwise-matching listings must
    not each trigger their own BALANCE_TOO_LOW attempt/notification once
    the pipeline is paused."""
    calls = []
    monitor, notifier, offer_state = await make_monitor(
        repo,
        unbounded=True,
        profile=SellerProfile(level=1, gift_count=0),
        offer_outcome=OfferOutcome(success=False, offer_id=1, error="BALANCE_TOO_LOW"),
        offer_calls=calls,
        selected_gift_types={1},
    )

    await monitor.handle_candidate(gift_id=1, unique=FakeUniqueGift("Gift-K1", offer_min_stars=50))
    await monitor.handle_candidate(gift_id=1, unique=FakeUniqueGift("Gift-K2", offer_min_stars=50))
    await monitor.handle_candidate(gift_id=1, unique=FakeUniqueGift("Gift-K3", offer_min_stars=50))

    assert len(calls) == 1  # only the first listing ever reached offer_executor
    assert notifier.paused_notifications == 1  # exactly one pause message, not three
    assert notifier.offers_failed == []


async def test_concurrent_listings_hitting_balance_too_low_only_pause_once(repo):
    """Directly exercises real concurrency (asyncio.gather, not just
    sequential awaits) — several different gift types processed at the same
    time, each independently hitting BALANCE_TOO_LOW at roughly the same
    moment, must still only ever produce exactly one pause notification.
    (The concurrent network calls themselves can't be un-sent once in
    flight — this proves the *notification* is deduplicated, which is the
    actual user-facing spam the guard exists to prevent.)"""
    calls = []

    async def slow_balance_too_low_executor(client, owner_peer, slug, price, duration_seconds):
        await asyncio.sleep(0.01)  # let every concurrent caller reach this point first
        calls.append(slug)
        return OfferOutcome(success=False, offer_id=1, error="BALANCE_TOO_LOW")

    monitor, notifier, offer_state = await make_monitor(
        repo,
        unbounded=True,
        profile=SellerProfile(level=1, gift_count=0),
        selected_gift_types={1, 2, 3},
    )
    monitor.offer_executor = slow_balance_too_low_executor

    await asyncio.gather(
        monitor.handle_candidate(gift_id=1, unique=FakeUniqueGift("Gift-M1", offer_min_stars=50)),
        monitor.handle_candidate(gift_id=2, unique=FakeUniqueGift("Gift-M2", offer_min_stars=50)),
        monitor.handle_candidate(gift_id=3, unique=FakeUniqueGift("Gift-M3", offer_min_stars=50)),
    )

    assert len(calls) == 3  # all three genuinely raced past the top-of-function guard together
    assert notifier.paused_notifications == 1  # yet exactly one "paused" broadcast went out
    assert notifier.offers_failed == []  # and zero "Offer tashlanmadi" messages
    assert offer_state.is_paused() is True


async def test_offer_run_stopped_while_paused_does_not_resume_on_its_own(repo):
    monitor, notifier, offer_state = await make_monitor(repo, unbounded=True)
    await offer_state.pause()
    await offer_state.stop()

    await monitor.check_balance_once()

    assert notifier.resumed_notifications == []


async def test_check_balance_once_does_nothing_when_not_paused(repo):
    calls = []

    async def tracking_balance_fetcher(client):
        calls.append(True)
        return 999

    monitor, notifier, offer_state = await make_monitor(repo, unbounded=True)
    monitor.balance_fetcher = tracking_balance_fetcher
    # active, but never paused

    await monitor.check_balance_once()

    assert calls == []  # never even bothered checking the balance
    assert notifier.resumed_notifications == []


async def test_check_balance_once_stays_paused_when_balance_still_low(repo):
    monitor, notifier, offer_state = await make_monitor(repo, unbounded=True, balance_amount=50)
    await offer_state.pause()
    await repo.set_offer_price(125)

    await monitor.check_balance_once()

    assert offer_state.is_paused() is True
    assert notifier.resumed_notifications == []


async def test_check_balance_once_resumes_when_balance_is_sufficient(repo):
    monitor, notifier, offer_state = await make_monitor(repo, unbounded=True, balance_amount=200)
    await offer_state.pause()
    await repo.set_offer_price(125)

    await monitor.check_balance_once()

    assert offer_state.is_paused() is False
    assert notifier.resumed_notifications == [(200, 125)]


async def test_check_balance_once_resumes_at_exact_threshold(repo):
    monitor, notifier, offer_state = await make_monitor(repo, unbounded=True, balance_amount=125)
    await offer_state.pause()
    await repo.set_offer_price(125)

    await monitor.check_balance_once()

    assert offer_state.is_paused() is False


async def test_check_balance_once_sends_only_one_resume_notification(repo):
    """Mirrors the pause-side race guard: resume() only returns True once."""
    monitor, notifier, offer_state = await make_monitor(repo, unbounded=True, balance_amount=200)
    await offer_state.pause()
    await repo.set_offer_price(125)

    await monitor.check_balance_once()
    await monitor.check_balance_once()  # a second tick after already-resumed

    assert len(notifier.resumed_notifications) == 1


async def test_offer_resumes_and_can_send_again_after_balance_recovers(repo):
    """End-to-end: pause on BALANCE_TOO_LOW, then once the balance-watch
    resumes the run, a fresh matching listing goes through normally again."""
    monitor, notifier, offer_state = await make_monitor(
        repo,
        unbounded=True,
        profile=SellerProfile(level=1, gift_count=0),
        offer_outcome=OfferOutcome(success=False, offer_id=1, error="BALANCE_TOO_LOW"),
    )

    await monitor.handle_candidate(gift_id=1, unique=FakeUniqueGift("Gift-L1", offer_min_stars=50))
    assert offer_state.is_paused() is True

    # balance recovers
    monitor.balance_fetcher = make_balance_fetcher(200)
    await monitor.check_balance_once()
    assert offer_state.is_paused() is False
    assert notifier.resumed_notifications == [(200, 125)]

    # and a new listing now goes through as a normal success
    monitor.offer_executor = make_offer_executor(OfferOutcome(success=True, offer_id=2))
    await monitor.handle_candidate(gift_id=1, unique=FakeUniqueGift("Gift-L2", offer_min_stars=50, title="Y"))
    assert len(notifier.offers_sent) == 1


# ------------------------------------------------------------- "Offer" folder

async def test_successful_offer_adds_seller_to_offer_folder(repo):
    folder = FakeDialogFilter()
    add_calls = []
    monitor, notifier, offer_state = await make_monitor(
        repo,
        unbounded=True,
        profile=SellerProfile(level=1, gift_count=0),
        offer_folder_finder=make_folder_finder(folder),
        offer_folder_adder=make_folder_adder(add_calls),
    )

    await monitor.handle_candidate(
        gift_id=1, unique=FakeUniqueGift("Gift-N1", offer_min_stars=50, owner_id="seller-1")
    )

    assert len(add_calls) == 1
    called_folder, called_peer = add_calls[0]
    assert called_folder is folder
    assert called_peer == "seller-1"


async def test_failed_offer_does_not_touch_the_folder(repo):
    add_calls = []
    monitor, notifier, offer_state = await make_monitor(
        repo,
        unbounded=True,
        profile=SellerProfile(level=1, gift_count=0),
        offer_outcome=OfferOutcome(success=False, offer_id=1, error="RESELL_STARS_TOO_FEW"),
        offer_folder_adder=make_folder_adder(add_calls),
    )

    await monitor.handle_candidate(gift_id=1, unique=FakeUniqueGift("Gift-N2", offer_min_stars=50))

    assert add_calls == []


async def test_missing_offer_folder_warns_admins_exactly_once(repo):
    monitor, notifier, offer_state = await make_monitor(
        repo,
        unbounded=True,
        profile=SellerProfile(level=1, gift_count=0),
        offer_folder_finder=make_folder_finder(None),  # not found
    )

    await monitor.handle_candidate(gift_id=1, unique=FakeUniqueGift("Gift-N3", offer_min_stars=50, title="A"))
    await monitor.handle_candidate(gift_id=1, unique=FakeUniqueGift("Gift-N4", offer_min_stars=50, title="B"))

    assert notifier.offer_folder_missing_count == 1  # not once per offer


async def test_offer_folder_lookup_failure_does_not_crash_the_pipeline(repo):
    async def raising_finder(client, name):
        raise RuntimeError("boom")

    monitor, notifier, offer_state = await make_monitor(
        repo,
        unbounded=True,
        profile=SellerProfile(level=1, gift_count=0),
        offer_folder_finder=raising_finder,
    )

    await monitor.handle_candidate(gift_id=1, unique=FakeUniqueGift("Gift-N5", offer_min_stars=50, title="C"))

    # the offer itself still went through and was reported successfully
    assert len(notifier.offers_sent) == 1


async def test_offer_folder_add_failure_does_not_crash_the_pipeline(repo):
    async def raising_adder(client, folder, peer):
        raise RuntimeError("boom")

    monitor, notifier, offer_state = await make_monitor(
        repo,
        unbounded=True,
        profile=SellerProfile(level=1, gift_count=0),
        offer_folder_adder=raising_adder,
    )

    await monitor.handle_candidate(gift_id=1, unique=FakeUniqueGift("Gift-N6", offer_min_stars=50, title="D"))

    assert len(notifier.offers_sent) == 1
