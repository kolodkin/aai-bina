"""In-memory push hub for remote control: one message queue per armed browser
channel, keyed by a random public id. SSE framing and disconnect handling live
in main.py."""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from .validation import presentation_error

LOCK_TTL_SECONDS = 30.0


@dataclass
class _Channel:
    queue: "asyncio.Queue[dict[str, Any]]" = field(default_factory=asyncio.Queue)
    # Advisory edit lock: "human" | "agent" | None. lock_touched is a monotonic
    # timestamp refreshed on acquire; a human lock older than LOCK_TTL_SECONDS is
    # treated as released (heartbeat lapsed / tab froze).
    lock_owner: str | None = None
    lock_touched: float = 0.0


# remote_id -> channel. Module-level, like connect.py's _sessions.
_channels: dict[str, _Channel] = {}


def _human_holds(channel: _Channel) -> bool:
    return (
        channel.lock_owner == "human"
        and (time.monotonic() - channel.lock_touched) < LOCK_TTL_SECONDS
    )


def acquire(remote_id: str, owner: str) -> tuple[bool, str]:
    """Take (or refresh) the edit lock for `owner`. Succeeds if free, already
    yours, or the current holder's TTL has lapsed. Otherwise returns the
    owner-named block reason."""
    channel = _channels.get(remote_id)
    if channel is None:
        return False, "unknown or inactive session"
    now = time.monotonic()
    expired = (now - channel.lock_touched) >= LOCK_TTL_SECONDS
    if channel.lock_owner in (None, owner) or expired:
        channel.lock_owner = owner
        channel.lock_touched = now
        return True, "acquired"
    who = "user" if channel.lock_owner == "human" else "agent"
    return False, f"blocked, {who} editing"


def release(remote_id: str, owner: str) -> tuple[bool, str]:
    """Release the lock only if `owner` holds it (idempotent)."""
    channel = _channels.get(remote_id)
    if channel is None:
        return False, "unknown or inactive session"
    if channel.lock_owner == owner:
        channel.lock_owner = None
    return True, "released"


def register() -> str:
    """Create a channel for a newly-armed browser session; return its public id.
    The id is random and unrelated to the qv_session cookie, so the session secret
    is never exposed to the agent."""
    remote_id = secrets.token_hex(8)
    _channels[remote_id] = _Channel()
    return remote_id


def unregister(remote_id: str) -> None:
    """Drop a channel (idempotent)."""
    _channels.pop(remote_id, None)


def push(remote_id: str, payload: dict[str, Any]) -> tuple[bool, str]:
    """Validate + lock-check + enqueue a payload for a channel. Ordered:
    unknown session -> invalid presentation -> human holds lock -> deliver."""
    channel = _channels.get(remote_id)
    if channel is None:
        return False, "unknown or inactive session"
    err = presentation_error(payload.get("order_by"), payload.get("fields"))
    if err is not None:
        return False, err
    if _human_holds(channel):
        return False, "blocked, user editing"
    channel.queue.put_nowait(payload)
    return True, "delivered"


async def next_message(remote_id: str, timeout: float) -> dict[str, Any] | None:
    """Wait up to `timeout` seconds for the next payload on a channel. Returns
    None on timeout or if the channel is gone."""
    channel = _channels.get(remote_id)
    if channel is None:
        return None
    try:
        return await asyncio.wait_for(channel.queue.get(), timeout)
    except asyncio.TimeoutError:
        return None
