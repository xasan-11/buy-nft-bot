from __future__ import annotations

from ..database.repository import Repository


class OfferState:
    """Persistent state for one user's "🎯 Offer boshlash" / "🛑 Offer
    to'xtatish" run, backed by that user's own row in the `users` table
    (see database/repository.py's per-user offer methods) so it survives a
    restart — mirrors MonitorState's persisted-flag-plus-in-memory-mirror
    approach, just scoped to a single Telegram user instead of the whole
    app.

    Every user (owner included) gets their own independent OfferState —
    see monitoring/offer_registry.py, which owns the in-memory instances so
    the rest of the app never constructs one directly.

    Deliberately has no target-count/"send N then stop" concept at all: the
    gift-type picker's Start always begins an unbounded run that only ever
    ends when this user taps Stop — that is the entire contract.
    """

    def __init__(self, repo: Repository, telegram_id: int):
        self._repo = repo
        self.telegram_id = telegram_id
        self._active = False
        self._sent = 0
        self._paused = False

    async def load(self) -> None:
        self._active = await self._repo.get_user_offer_active(self.telegram_id)
        self._sent = await self._repo.get_user_offer_sent_count(self.telegram_id)
        self._paused = await self._repo.get_user_offer_paused(self.telegram_id)

    def is_active(self) -> bool:
        """False as soon as this user's "🛑 Offer to'xtatish" lands —
        checked before every seller-profile lookup and again immediately
        before spending Stars, so a stop always takes effect right away."""
        return self._active

    def is_paused(self) -> bool:
        """True once a BALANCE_TOO_LOW response has auto-paused sending for
        this user — independent of is_active(): the run itself is still
        "on", it's just not allowed to spend this user's Stars until their
        balance recovers. Checked at the very top of offer evaluation so a
        paused run does nothing at all for every subsequent listing (no
        repeated attempts/messages)."""
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
        await self._repo.start_user_offer_run(self.telegram_id)
        await self._repo.set_user_offer_paused(self.telegram_id, False)

    async def stop(self) -> None:
        self._active = False
        await self._repo.stop_user_offer_run(self.telegram_id)

    async def record_sent(self) -> int:
        self._sent = await self._repo.increment_user_offer_sent_count(self.telegram_id)
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
        await self._repo.set_user_offer_paused(self.telegram_id, True)
        return True

    async def resume(self) -> bool:
        """Mirrors pause(): True only for the caller that actually
        transitions paused->active again."""
        if not self._paused:
            return False
        self._paused = False
        await self._repo.set_user_offer_paused(self.telegram_id, False)
        return True
