from src.notifications.notifier import Notifier
from tests.conftest import OWNER_ID


class RecordingBotClient:
    def __init__(self):
        self.sent = []

    async def send_message(self, user_id, text, buttons=None):
        self.sent.append((user_id, text, buttons))


async def test_notify_ignored_sends_no_telegram_message(repo):
    bot_client = RecordingBotClient()
    notifier = Notifier(bot_client, repo)

    await notifier.notify_ignored("Gift-1", 999, 200, "examplechannel", "price exceeds maximum")

    assert bot_client.sent == []


async def test_notify_purchased_still_sends_to_admins(repo):
    bot_client = RecordingBotClient()
    notifier = Notifier(bot_client, repo)

    await notifier.notify_purchased("Gift-2", 150, "examplechannel")

    assert len(bot_client.sent) == 1
    assert bot_client.sent[0][0] == OWNER_ID
    assert "purchased" in bot_client.sent[0][1].lower()


async def test_notify_failed_still_sends_to_admins(repo):
    bot_client = RecordingBotClient()
    notifier = Notifier(bot_client, repo)

    await notifier.notify_failed("Gift-3", 150, "examplechannel", "BALANCE_TOO_LOW")

    assert len(bot_client.sent) == 1
    assert "failed" in bot_client.sent[0][1].lower()


async def test_notify_listing_checked_sends_formatted_message(repo):
    bot_client = RecordingBotClient()
    notifier = Notifier(bot_client, repo)

    await notifier.notify_listing_checked("Plush Pepe", 250, "https://t.me/nft/PlushPepe-1")

    assert len(bot_client.sent) == 1
    text = bot_client.sent[0][1]
    assert "Tekshirilmoqda: Plush Pepe" in text
    assert "250 ⭐" in text
    assert "https://t.me/nft/PlushPepe-1" in text


async def test_notify_listings_checked_batch_sends_nothing_when_empty(repo):
    bot_client = RecordingBotClient()
    notifier = Notifier(bot_client, repo)

    await notifier.notify_listings_checked_batch([], interval=5)

    assert bot_client.sent == []


async def test_notify_listings_checked_batch_formats_all_items(repo):
    bot_client = RecordingBotClient()
    notifier = Notifier(bot_client, repo)

    items = [
        ("Plush Pepe", 100, "https://t.me/nft/PlushPepe-1"),
        ("Duck Hat", 150, "https://t.me/nft/DuckHat-2"),
    ]
    await notifier.notify_listings_checked_batch(items, interval=5)

    assert len(bot_client.sent) == 1
    text = bot_client.sent[0][1]
    assert "Tekshiruv (5s)" in text
    assert "Topildi: 2 ta listing" in text
    assert "1. Plush Pepe — 100 ⭐ — https://t.me/nft/PlushPepe-1" in text
    assert "2. Duck Hat — 150 ⭐ — https://t.me/nft/DuckHat-2" in text


async def test_notify_listings_checked_batch_truncates_long_lists(repo):
    bot_client = RecordingBotClient()
    notifier = Notifier(bot_client, repo)

    items = [(f"Gift {i}", 100, f"link{i}") for i in range(25)]
    await notifier.notify_listings_checked_batch(items, interval=5)

    text = bot_client.sent[0][1]
    assert "Topildi: 25 ta listing" in text
    assert "20. Gift 19" in text
    assert "21. Gift 20" not in text
    assert "+5 ta yana" in text


async def test_notify_offer_sent_includes_nft_link_in_text(repo):
    bot_client = RecordingBotClient()
    notifier = Notifier(bot_client, repo)

    await notifier.notify_offer_sent(
        "Plush Pepe", 125, 6, "https://t.me/nft/PlushPepe-1", "https://t.me/theseller"
    )

    assert len(bot_client.sent) == 1
    text = bot_client.sent[0][1]
    assert "Offer tashlandi: Plush Pepe" in text
    assert "125⭐" in text
    assert "6 soat" in text
    assert "https://t.me/nft/PlushPepe-1" in text


async def test_notify_offer_sent_includes_both_buttons_when_seller_link_known(repo):
    bot_client = RecordingBotClient()
    notifier = Notifier(bot_client, repo)

    await notifier.notify_offer_sent(
        "Plush Pepe", 125, 6, "https://t.me/nft/PlushPepe-1", "https://t.me/theseller"
    )

    buttons = bot_client.sent[0][2]
    assert len(buttons) == 1  # one row
    assert len(buttons[0]) == 2  # two buttons side by side
    seller_button, nft_button = buttons[0]
    assert seller_button.text == "👤 Sotuvchi profiliga o'tish"
    assert seller_button.type.url == "https://t.me/theseller"
    assert nft_button.text == "🎁 NFT'ni ko'rish"
    assert nft_button.type.url == "https://t.me/nft/PlushPepe-1"


async def test_notify_offer_sent_omits_seller_button_when_link_unknown(repo):
    bot_client = RecordingBotClient()
    notifier = Notifier(bot_client, repo)

    await notifier.notify_offer_sent(
        "Plush Pepe", 125, 6, "https://t.me/nft/PlushPepe-1", seller_profile_link=None
    )

    buttons = bot_client.sent[0][2]
    assert len(buttons) == 1
    assert len(buttons[0]) == 1  # only the NFT button
    assert buttons[0][0].text == "🎁 NFT'ni ko'rish"


async def test_many_ignored_notifications_never_hit_send_message(repo):
    """Regression test for the flood-wait incident: hundreds of ignored
    listings in a row must never translate into hundreds of outgoing
    Telegram messages."""
    bot_client = RecordingBotClient()
    notifier = Notifier(bot_client, repo)

    for i in range(500):
        await notifier.notify_ignored(f"Gift-{i}", 9999, 200, "market", "price exceeds maximum")

    assert bot_client.sent == []
