from __future__ import annotations

import asyncio
import logging
import secrets
import struct
import time
from dataclasses import dataclass
from typing import Optional

from telethon.errors import FloodWaitError, RPCError
from telethon.tl.tlobject import TLObject, TLRequest

logger = logging.getLogger("paid_reaction")

# messages.sendPaidReaction is not present in Telethon's generated schema —
# verified against the installed/latest released Telethon (1.45.0 on
# PyPI): `grep -ri paidreaction` over the whole package finds nothing at
# all, so it has to be hand-written here rather than imported from
# telethon.tl.functions.messages. Written against the official schema
# published at https://core.telegram.org/method/messages.sendPaidReaction:
#
#   messages.sendPaidReaction#58bbcb50 flags:# peer:InputPeer msg_id:int
#       count:int random_id:long private:flags.0?PaidReactionPrivacy
#       = Updates;
#
# `private` (anonymity) is always omitted here (flags stays 0), so every
# reaction is sent under the account's own default privacy setting — this
# project has no UI for choosing anonymous vs. shown. The binary layout
# below mirrors Telethon's own generated request classes exactly (see e.g.
# telethon.tl.functions.messages.SendReactionRequest) — constructor id
# first as a little-endian uint32, then each field in schema order.
class SendPaidReactionRequest(TLRequest):
    CONSTRUCTOR_ID = 0x58BBCB50
    SUBCLASS_OF_ID = 0x8AF52AAC  # Updates — same value Telethon's own generated Updates-returning requests use

    def __init__(self, peer, msg_id: int, count: int, random_id: int):
        self.peer = peer
        self.msg_id = msg_id
        self.count = count
        self.random_id = random_id

    async def resolve(self, client, utils):
        self.peer = utils.get_input_peer(await client.get_input_entity(self.peer))

    def to_dict(self):
        return {
            "_": "SendPaidReactionRequest",
            "peer": self.peer.to_dict() if isinstance(self.peer, TLObject) else self.peer,
            "msg_id": self.msg_id,
            "count": self.count,
            "random_id": self.random_id,
        }

    def _bytes(self):
        return b"".join((
            struct.pack("<I", self.CONSTRUCTOR_ID),
            struct.pack("<I", 0),  # flags — `private` is always omitted
            self.peer._bytes(),
            struct.pack("<i", self.msg_id),
            struct.pack("<i", self.count),
            struct.pack("<q", self.random_id),
        ))

    @classmethod
    def from_reader(cls, reader):
        reader.read_int()  # flags — `private` is never read back here
        peer = reader.tgread_object()
        msg_id = reader.read_int()
        count = reader.read_int()
        random_id = reader.read_long()
        return cls(peer=peer, msg_id=msg_id, count=count, random_id=random_id)


def _new_random_id() -> int:
    """Per the official docs' recommended scheme: "(time() << 32) |
    random_lower_32bits" — only needs to be unique per call, used by
    Telegram purely for de-duplication."""
    return (int(time.time()) << 32) | secrets.randbits(32)


# Telegram's own paid-reaction picker caps a single tap at 2500 Stars (see
# https://core.telegram.org/api/reactions — "Users can choose how many
# Stars to send (1 to 2500 per reaction)"). The true server-enforced
# ceiling is actually the dynamic `stars_paid_reaction_amount_max`
# app-config value, which this project does not fetch, so 2500 is used
# here only as a practical per-call chunk size: if the server ever rejects
# a chunk this size for any reason, send_all_stars_as_reaction below stops
# immediately and reports exactly how much was actually sent, rather than
# guessing at a smaller size and retrying blindly.
MAX_STARS_PER_REACTION_CALL = 2500
MAX_FLOOD_WAIT_RETRIES = 3


@dataclass
class PaidReactionOutcome:
    sent: int
    remaining_balance: int
    error: Optional[str] = None


async def _send_one_chunk(client, peer, msg_id: int, count: int) -> Optional[str]:
    """Sends one messages.sendPaidReaction call for `count` Stars, retrying
    on FloodWaitError (waiting exactly what Telegram asked) up to
    MAX_FLOOD_WAIT_RETRIES times — mirrors the retry pattern already used
    for market scanning (see monitoring/market_monitor.py). Returns None
    on success, else an error string."""
    for attempt in range(1, MAX_FLOOD_WAIT_RETRIES + 1):
        try:
            await client(SendPaidReactionRequest(
                peer=peer, msg_id=msg_id, count=count, random_id=_new_random_id(),
            ))
            return None
        except FloodWaitError as e:
            logger.warning(
                "FloodWait while sending paid reaction: waiting %ss (attempt %d/%d)",
                e.seconds, attempt, MAX_FLOOD_WAIT_RETRIES,
            )
            await asyncio.sleep(e.seconds)
        except RPCError as e:
            return str(e)
    return "flood wait: exceeded max retries"


async def send_all_stars_as_reaction(client, peer, msg_id: int, balance: int) -> PaidReactionOutcome:
    """Sends `balance` Stars as a paid reaction to one message, chunked at
    MAX_STARS_PER_REACTION_CALL per call and looping until the whole
    balance is sent or a chunk fails. On failure, whatever was already
    sent in earlier chunks stays sent (Telegram has no "undo") — the
    outcome reports exactly how much went through and how much is left.
    """
    remaining = balance
    sent_total = 0
    while remaining > 0:
        chunk = min(remaining, MAX_STARS_PER_REACTION_CALL)
        error = await _send_one_chunk(client, peer, msg_id, chunk)
        if error:
            return PaidReactionOutcome(sent=sent_total, remaining_balance=remaining, error=error)
        sent_total += chunk
        remaining -= chunk

    return PaidReactionOutcome(sent=sent_total, remaining_balance=0)
