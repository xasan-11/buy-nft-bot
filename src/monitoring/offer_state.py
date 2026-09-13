from __future__ import annotations

from ..database.repository import Repository


class OfferState:
    """Persistent state for the "🎯 Offer boshlash" / "🛑 Offer to'xtatish"
    run, backed by application_settings so it survives a restart — mirrors
    MonitorState's persisted-flag-plus-in-memory-mirror approach.

    Deliberately has no target-count/"send N then stop" concept at all: the
    gift-type picker's Start always begins an unbounded run that only ever
    ends when an admin taps Stop — that is the entire contract.
    """

    def __init__(self, repo: Repository):
        self._repo = repo
        self._active = False
        self._sent = 0
        self._paused = False

    async def load(self) -> None:
        self._active = await self._repo.get_offer_active()
        self._sent = await self._repo.get_offer_sent_count()
        self._paused = await self._repo.get_offer_paused()

    def is_active(self) -> bool:
        """False as soon as "🛑 Offer to'xtatish" lands — checked before
        every seller-profile lookup and again immediately before spending
        Stars, so a stop always takes effect right away."""
        return self._active

    def is_paused(self) -> bool:
        """True once a BALANCE_TOO_LOW response has auto-paused sending —
        independent of is_active(): the run itself is still "on", it's just
        not allowed to spend Stars until the balance recovers. Checked at
        the very top of _maybe_send_offer so a paused run does nothing at
        all for every subsequent listing (no repeated attempts/messages)."""
        return self._paused

    @property
    def sent(self) -> int:
        return self._sent

    async def start(self) -> None:
        self._active = True
        self._sent = 0
        # A fresh explicit Start always gets a clean slate — a stale pause
        # from a previous run must never silently block a brand new one.
        self._paused = False
        await self._repo.start_offer_run()
        await self._repo.set_offer_paused(False)

    async def stop(self) -> None:
        self._active = False
        await self._repo.stop_offer_run()

    async def record_sent(self) -> int:
        self._sent = await self._repo.increment_offer_sent_count()
        return self._sent

    async def pause(self) -> bool:
        """Returns True only for the caller that actually transitions
        active->paused, so exactly one caller sends the "paused" broadcast
        even if several concurrently-scanned listings hit BALANCE_TOO_LOW
        at the same moment (the check-then-set below never yields to
        another task in between, since there is no `await` between them —
        asyncio only switches tasks at an `await` point)."""
        if self._paused:
            return False
        self._paused = True
        await self._repo.set_offer_paused(True)
        return True

    async def resume(self) -> bool:
        """Mirrors pause(): True only for the caller that actually
        transitions paused->active again."""
        if not self._paused:
            return False
        self._paused = False
        await self._repo.set_offer_paused(False)
        return True
