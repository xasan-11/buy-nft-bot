from src.notifications.notifier import Notifier
from tests.conftest import OWNER_ID


class RecordingBotClient:
    def __init__(self):
        self.sent = []

    async def send_message(self, user_id, text):
        self.sent.append((user_id, text))


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


async def test_many_ignored_notifications_never_hit_send_message(repo):
    """Regression test for the flood-wait incident: hundreds of ignored
    listings in a row must never translate into hundreds of outgoing
    Telegram messages."""
    bot_client = RecordingBotClient()
    notifier = Notifier(bot_client, repo)

    for i in range(500):
        await notifier.notify_ignored(f"Gift-{i}", 9999, 200, "market", "price exceeds maximum")

    assert bot_client.sent == []
