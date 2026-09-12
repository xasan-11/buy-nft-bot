from __future__ import annotations

import asyncio
import logging

from .bot.bot_app import register_bot_handlers
from .bot.menu import set_default_commands, sync_all_admin_menus
from .config.settings import Settings
from .database.repository import MARKET_MONITORING_KEY, Repository
from .monitoring.channel_monitor import ChannelMonitor, MonitorState
from .monitoring.market_monitor import MarketMonitor
from .notifications.notifier import Notifier
from .telegram.client import build_bot_client, build_user_client
from .telegram.updates import register_handlers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("main")


async def run() -> None:
    settings = Settings.load()

    repo = Repository(settings.db_path)
    await repo.connect(settings.owner_id, settings.max_nft_price, settings.market_poll_concurrency)

    user_client = build_user_client(settings)
    await user_client.start(phone=settings.phone)
    logger.info("User account connected.")

    bot_client = build_bot_client(settings)
    await bot_client.start(bot_token=settings.bot_token)
    logger.info("Control bot connected.")

    await set_default_commands(bot_client)
    await sync_all_admin_menus(bot_client, repo)

    notifier = Notifier(bot_client, repo)

    state = MonitorState(repo)
    await state.load()
    monitor = ChannelMonitor(user_client, repo, state, settings.max_nft_price, notifier)
    await monitor.refresh_channels()
    register_handlers(user_client, monitor)

    market_state = MonitorState(repo, key=MARKET_MONITORING_KEY)
    await market_state.load()
    market_monitor = MarketMonitor(
        user_client, repo, market_state, notifier, poll_interval=settings.market_poll_interval_seconds
    )

    register_bot_handlers(bot_client, repo, monitor, state, notifier, market_state)

    logger.info(
        "Bootstrapped. Channel monitoring: %s, market monitoring: %s, channels: %d",
        "RUNNING" if state.is_running() else "STOPPED",
        "RUNNING" if market_state.is_running() else "STOPPED",
        len(monitor.channel_ids),
    )

    try:
        await asyncio.gather(
            user_client.run_until_disconnected(),
            bot_client.run_until_disconnected(),
            market_monitor.run_forever(),
        )
    finally:
        await repo.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
