from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# t.me/<username>/<msg_id>, optionally with an "s/" preview prefix (e.g.
# t.me/s/channel/123) — a leading https://, http://, or no scheme at all
# are all accepted, matching how people actually paste Telegram links.
_PRIVATE_RE = re.compile(r"^(?:https?://)?t\.me/c/(\d+)/(\d+)/?(?:\?.*)?$")
# t.me/c/<internal_channel_id>/<msg_id> — for a private channel; only
# resolvable if the logged-in account has already seen/joined it, since
# Telegram gives no other way to look up a private channel by this id.
_PUBLIC_RE = re.compile(r"^(?:https?://)?t\.me/(?:s/)?([A-Za-z][A-Za-z0-9_]{3,31})/(\d+)/?(?:\?.*)?$")


@dataclass
class PostRef:
    message_id: int
    channel_username: Optional[str] = None
    channel_id: Optional[int] = None  # only set for t.me/c/<id>/... links


def parse_post_link(raw: Optional[str]) -> Optional[PostRef]:
    """Parses a t.me post link into (channel, message_id). Returns None for
    anything that doesn't match either recognized shape — never guesses at
    a channel/message id from free-form text."""
    if not raw:
        return None
    raw = raw.strip()

    match = _PRIVATE_RE.match(raw)
    if match:
        return PostRef(channel_id=int(match.group(1)), message_id=int(match.group(2)))

    match = _PUBLIC_RE.match(raw)
    if match:
        return PostRef(channel_username=match.group(1), message_id=int(match.group(2)))

    return None
