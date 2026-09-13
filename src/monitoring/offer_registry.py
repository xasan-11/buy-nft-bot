from __future__ import annotations

from typing import Dict, List, Tuple

from ..database.repository import Repository
from .offer_state import OfferState


class OfferRegistry:
    """In-memory cache of one OfferState per Telegram user, backed by that
    user's row in the `users` table (see OfferState/database/repository.py)
    — the multi-tenant replacement for the single global OfferState the app
    used before, keyed by telegram_id so every logged-in user's "🎯 Offer
    boshlash" run (and its pause/resume/sent-count) is fully independent
    and never leaks into another user's run.

    Shared by bot_app.py (start/stop a specific user's run from the
    gift-picker flow) and MarketMonitor (iterate every currently-active
    user's run against each freshly-detected listing) — both always go
    through get()/active() here rather than constructing an OfferState
    directly, so there is only ever one in-memory instance per user.
    """

    def __init__(self, repo: Repository):
        self._repo = repo
        self._states: Dict[int, OfferState] = {}

    async def get(self, telegram_id: int) -> OfferState:
        state = self._states.get(telegram_id)
        if state is None:
            state = OfferState(self._repo, telegram_id)
            await state.load()
            self._states[telegram_id] = state
        return state

    async def active(self) -> List[Tuple[int, OfferState]]:
        """(telegram_id, OfferState) pairs whose run is currently active,
        among every user this process has loaded so far — see
        preload_active() for populating this at startup with users whose
        run was already active before the process last restarted."""
        return [(uid, s) for uid, s in self._states.items() if s.is_active()]

    async def preload_active(self) -> None:
        """Loads an OfferState for every user whose offer run was already
        active when the process last stopped, so a restart resumes their
        run without waiting for them to touch anything (their own
        TelegramClient still has to reconnect/reauthorize independently —
        see telegram/user_manager.py — before MarketMonitor will actually
        act on their run again)."""
        for row in await self._repo.list_users_with_active_offer():
            await self.get(row["telegram_id"])
