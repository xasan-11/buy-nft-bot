from __future__ import annotations

import asyncio
import logging
import signal

from .bot.bot_app import register_bot_handlers
from .bot.menu import set_default_commands, sync_all_admin_menus
from .config.settings import Settings
from .database.repository import MARKET_MONITORING_KEY, Repository
from .monitoring.channel_monitor import ChannelMonitor, MonitorState
from .monitoring.market_monitor import MarketMonitor
from .monitoring.offer_state import OfferState
from .notifications.notifier import Notifier
from .telegram.client import build_bot_client, build_user_client
from .telegram.login_flow import SESSION_STRING_SETTING_KEY, UserLoginCoordinator
from .telegram.offer_decline import register_offer_decline_handlers
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

    # The control bot is started first and independently of the user
    # account: admin commands (/status, /add, etc.) work immediately even
    # while the user account still needs to complete /login below.
    bot_client = build_bot_client(settings)
    await bot_client.start(bot_token=settings.bot_token)
    logger.info("Control bot connected.")

    await set_default_commands(bot_client)
    await sync_all_admin_menus(bot_client, repo)

    notifier = Notifier(bot_client, repo)

    # A session saved by a previous /login (see login_flow.py) always wins
    # over TELEGRAM_SESSION_STRING from generate_session.py — it's the most
    # recently proven-valid one. Either may be empty/stale; either way
    # UserLoginCoordinator.start() below checks is_user_authorized() itself
    # and falls back to the bot-mediated login flow rather than failing.
    stored_session = await repo.get_setting(SESSION_STRING_SETTING_KEY, "")
    user_client = build_user_client(settings, stored_session or settings.session_string)

    login_coordinator = UserLoginCoordinator(user_client, bot_client, repo, settings)
    login_coordinator.register_handlers()

    state = MonitorState(repo)
    await state.load()
    monitor = ChannelMonitor(user_client, repo, state, settings.max_nft_price, notifier)
    register_handlers(user_client, monitor)

    market_state = MonitorState(repo, key=MARKET_MONITORING_KEY)
    await market_state.load()
    offer_state = OfferState(repo)
    await offer_state.load()
    market_monitor = MarketMonitor(
        user_client,
        repo,
        market_state,
        notifier,
        poll_interval=settings.market_poll_interval_seconds,
        offer_state=offer_state,
    )

    register_bot_handlers(bot_client, repo, monitor, state, notifier, market_state, offer_state)
    register_offer_decline_handlers(user_client, repo)

    logger.info(
        "Bootstrapped. Channel monitoring: %s, market monitoring: %s, channels: %d "
        "(user account monitoring/purchasing waits for login).",
        "RUNNING" if state.is_running() else "STOPPED",
        "RUNNING" if market_state.is_running() else "STOPPED",
        len(monitor.channel_ids),
    )

    async def user_dependent_services() -> None:
        """Everything that actually touches the user account. Waits for
        login_coordinator.ready so a fresh deploy with no valid session yet
        never starts monitoring/purchasing/offers before /login finishes —
        the control bot itself (started above) is unaffected and keeps
        answering admin commands the whole time."""
        await login_coordinator.ready.wait()
        logger.info("User account authorized — starting monitoring.")
        await monitor.refresh_channels()
        await asyncio.gather(
            user_client.run_until_disconnected(),
            market_monitor.run_forever(),
            market_monitor.verbose_flush_loop(),
            market_monitor.balance_watch_loop(),
        )

    async def expire_offers_loop() -> None:
        """Telegram auto-refunds a declined/expired offer on its own side;
        this only keeps our local star_gift_offers status in sync so
        /status and get_offer_stats() don't show stale PENDING rows."""
        while True:
            await asyncio.sleep(60)
            try:
                await repo.expire_stale_offers()
            except Exception:
                logger.exception("Failed to sweep expired offers")

    # Ctrl+C (SIGINT) or a platform-sent SIGTERM (Railway sends this before
    # SIGKILL on every redeploy/restart) both just flip this event rather
    # than interrupting whatever coroutine happens to be running — that's
    # what lets the `finally` below always run and actually close the
    # sqlite connection (data/app.db) instead of leaving it (and its WAL
    # lock) held by an orphaned process, which is what caused "database is
    # locked" on the next start.
    stop_event = asyncio.Event()

    def _request_shutdown(signum, _frame=None) -> None:
        logger.info("Received signal %s — shutting down gracefully.", signum)
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _request_shutdown)
        except (ValueError, OSError, AttributeError):
            # Not every signal is interceptable on every platform (notably
            # SIGTERM's behavior varies on Windows) — best effort only, the
            # try/finally below is still the real safety net for anything
            # that *does* let this coroutine unwind normally.
            logger.debug("Could not register a handler for signal %s here", sig)

    # login_coordinator.start() deliberately finishes quickly (it kicks off
    # the flow and returns — it does not wait for /code / /password), so it
    # must NOT sit in the FIRST_COMPLETED set below: that set's purpose is
    # "a long-running task died, time to shut down", and this task finishing
    # on schedule is not that. It's still tracked so shutdown can cancel and
    # await it like everything else.
    login_task = asyncio.create_task(login_coordinator.start())

    background_tasks = [
        asyncio.create_task(bot_client.run_until_disconnected()),
        asyncio.create_task(user_dependent_services()),
        asyncio.create_task(expire_offers_loop()),
    ]
    stop_waiter = asyncio.create_task(stop_event.wait())

    try:
        await asyncio.wait([stop_waiter, *background_tasks], return_when=asyncio.FIRST_COMPLETED)
    finally:
        stop_waiter.cancel()
        login_task.cancel()
        for task in background_tasks:
            task.cancel()
        await asyncio.gather(*background_tasks, login_task, stop_waiter, return_exceptions=True)

        await repo.close()
        for client, name in ((user_client, "user client"), (bot_client, "bot client")):
            try:
                await client.disconnect()
            except Exception:
                logger.exception("Error disconnecting %s during shutdown", name)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
