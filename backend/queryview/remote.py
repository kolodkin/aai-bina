"""In-memory push hub for the remote-control layer: one message queue per armed
browser channel, keyed by a random public id. No HTTP-server or ClickHouse
concerns live here — the SSE framing and disconnect handling live in main.py."""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _Channel:
    queue: "asyncio.Queue[dict[str, Any]]" = field(default_factory=asyncio.Queue)


# remote_id -> channel. Module-level, like connect.py's _sessions.
_channels: dict[str, _Channel] = {}


def register() -> str:
    """Create a channel for a newly-armed browser session; return its public id.
    The id is random and unrelated to the qv_session cookie, so the session
    secret is never exposed to the agent."""
    remote_id = secrets.token_hex(8)
    _channels[remote_id] = _Channel()
    return remote_id


def unregister(remote_id: str) -> None:
    """Drop a channel (idempotent)."""
    _channels.pop(remote_id, None)


def push(remote_id: str, payload: dict[str, Any]) -> tuple[bool, str]:
    """Enqueue a payload for a channel. (False, message) if no such channel."""
    channel = _channels.get(remote_id)
    if channel is None:
        return False, "unknown or inactive session"
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
