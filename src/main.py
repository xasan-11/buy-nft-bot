from __future__ import annotations

import asyncio
import logging
import signal

from .bot.bot_app import register_bot_handlers
from .bot.menu import set_default_commands, sync_all_menus
from .config.settings import Settings
from .database.repository import MARKET_MONITORING_KEY, Repository
from .monitoring.channel_monitor import ChannelMonitor, MonitorState
from .monitoring.market_monitor import MarketMonitor
from .monitoring.offer_registry import OfferRegistry
from .notifications.notifier import Notifier
from .telegram.client import build_bot_client, build_user_client
from .telegram.updates import register_handlers
from .telegram.user_manager import UserSessionManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("main")


async def run() -> None:
    settings = Settings.load()

    repo = Repository(settings.db_path)
    await repo.connect(settings.owner_id, settings.max_nft_price, settings.market_poll_concurrency)

    # The control bot is started first and independently of every user
    # account: owner commands (/status, /add, etc.) work immediately even
    # while the owner's own account still needs to complete /login below,
    # and any other user can /login and start offer-tashlash without ever
    # touching the owner's account at all.
    bot_client = build_bot_client(settings)
    await bot_client.start(bot_token=settings.bot_token)
    logger.info("Control bot connected.")

    await set_default_commands(bot_client)
    await sync_all_menus(bot_client, repo)

    notifier = Notifier(bot_client, repo)

    session_manager = UserSessionManager(bot_client, repo, settings)
    session_manager.register_handlers()

    # The owner's client is special only in that it's created and connected
    # eagerly, right here, so it can be handed to ChannelMonitor/
    # MarketMonitor immediately below — exactly like the single-tenant app
    # did. A session saved by a previous /login always wins over
    # TELEGRAM_SESSION_STRING from generate_session.py (it's the most
    # recently proven-valid one); either may be empty/stale, and either way
    # session_manager.restore_all() below checks is_user_authorized() itself
    # and falls back to the bot-mediated login flow rather than failing.
    owner_session = await repo.get_user_session(settings.owner_id)
    user_client = build_user_client(settings, owner_session or settings.session_string)
    session_manager.clients[settings.owner_id] = user_client

    state = MonitorState(repo)
    await state.load()
    monitor = ChannelMonitor(user_client, repo, state, settings.max_nft_price, notifier)
    register_handlers(user_client, monitor)

    market_state = MonitorState(repo, key=MARKET_MONITORING_KEY)
    await market_state.load()

    # Multi-tenant auto-offer pipeline: listing *scanning* stays on the
    # owner's account (one shared API-request stream — see
    # monitoring/market_monitor.py's module docstring), but every
    # currently-active user is independently evaluated and offered to
    # through their own account — see offer_registry.py/user_manager.py.
    offer_registry = OfferRegistry(repo)
    await offer_registry.preload_active()
    market_monitor = MarketMonitor(
        user_client,
        repo,
        market_state,
        notifier,
        poll_interval=settings.market_poll_interval_seconds,
        offer_registry=offer_registry,
        session_manager=session_manager,
    )

    register_bot_handlers(
        bot_client, repo, monitor, state, notifier, market_state, offer_registry, session_manager
    )

    logger.info(
        "Bootstrapped. Channel monitoring: %s, market monitoring: %s, channels: %d "
        "(user accounts wait for their own /login before monitoring/purchasing/offers).",
        "RUNNING" if state.is_running() else "STOPPED",
        "RUNNING" if market_state.is_running() else "STOPPED",
        len(monitor.channel_ids),
    )

    async def bootstrap_sessions() -> None:
        """Reconnects every user (owner included) who already completed
        /login in a previous run, then — if the owner still isn't
        authorized — either auto-kicks off the phone/code flow (when
        TELEGRAM_PHONE is configured) or asks them to /login manually.
        Every other user simply stays unauthorized until they /login
        themselves — nothing here waits on or blocks for them."""
        await session_manager.restore_all()
        if not session_manager.is_ready(settings.owner_id):
            await session_manager.handle_login_command(settings.owner_id, settings.phone, None)

    async def user_dependent_services() -> None:
        """Everything that actually touches the owner's account. Waits for
        the owner's client to be authorized so a fresh deploy with no valid
        session yet never starts channel/market monitoring before /login
        finishes — the control bot itself (started above), and every other
        user's own offer pipeline, are unaffected and keep working the
        whole time."""
        await session_manager.wait_ready(settings.owner_id)
        logger.info("Owner account authorized — starting channel/market monitoring.")
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

    # bootstrap_sessions() deliberately finishes quickly for most users (it
    # kicks off whatever flow applies and returns — it does not wait for
    # /code / /password), so it must NOT sit in the FIRST_COMPLETED set
    # below: that set's purpose is "a long-running task died, time to shut
    # down", and this task finishing on schedule is not that. It's still
    # tracked so shutdown can cancel and await it like everything else.
    bootstrap_task = asyncio.create_task(bootstrap_sessions())

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
        bootstrap_task.cancel()
        for task in background_tasks:
            task.cancel()
        for task in session_manager.background_tasks:
            task.cancel()
        await asyncio.gather(
            *background_tasks, bootstrap_task, stop_waiter,
            *session_manager.background_tasks, return_exceptions=True,
        )

        await repo.close()
        # session_manager.clients already includes the owner's client (the
        # same user_client object seeded above), so this alone covers every
        # currently-connected user account.
        for telegram_id, client in session_manager.clients.items():
            try:
                await client.disconnect()
            except Exception:
                logger.exception("Error disconnecting client for user %s during shutdown", telegram_id)
        try:
            await bot_client.disconnect()
        except Exception:
            logger.exception("Error disconnecting bot client during shutdown")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
