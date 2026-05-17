from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from fastapi import WebSocket

log = logging.getLogger(__name__)

# Per-connection send buffer size. WS sends are normally instant, so this
# only matters if the client is slow. If it overflows, oldest message is
# dropped (clients refetch on any message so missing one is harmless).
_OUTBOX_MAXSIZE = 32

# Heartbeat interval — server pings every 25s. Most NAT/proxies drop idle
# TCP after 30s, so 25 keeps us safely under that.
HEARTBEAT_INTERVAL_SECS = 25


@dataclass
class _Connection:
    """One live WebSocket attached to a user."""
    ws: WebSocket
    user_id: int
    loop: asyncio.AbstractEventLoop
    outbox: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=_OUTBOX_MAXSIZE))
    # When the writer task is running we keep a handle so we can cancel it
    # on disconnect.
    writer_task: Optional[asyncio.Task] = None


class WebSocketManager:
    """Process-local registry of live WebSocket connections.

    Not thread-safe in the strict sense — `_conns` is mutated from the
    event loop only. Publishers running in worker threads go through
    `publish()`, which uses `call_soon_threadsafe` to reach the loop.
    """

    def __init__(self) -> None:
        # user_id → list of _Connection. A user can have multiple tabs
        # open — each gets its own connection.
        self._conns: Dict[int, List[_Connection]] = defaultdict(list)
        self._lock = asyncio.Lock()  # only held briefly during register/unregister

    # ── Lifecycle ────────────────────────────────────────────────────────
    async def register(self, ws: WebSocket, user_id: int) -> _Connection:
        """Accept the WS, attach it to user_id, start the writer task.
        Must be called AFTER `await ws.accept()`."""
        loop = asyncio.get_running_loop()
        conn = _Connection(ws=ws, user_id=user_id, loop=loop)
        async with self._lock:
            self._conns[user_id].append(conn)
        conn.writer_task = asyncio.create_task(self._writer_loop(conn))
        log.debug("WS register user=%s total=%d", user_id, len(self._conns[user_id]))
        return conn

    async def unregister(self, conn: _Connection) -> None:
        """Remove a connection and cancel its writer task."""
        async with self._lock:
            lst = self._conns.get(conn.user_id, [])
            if conn in lst:
                lst.remove(conn)
            if not lst:
                self._conns.pop(conn.user_id, None)
        if conn.writer_task and not conn.writer_task.done():
            conn.writer_task.cancel()
        log.debug(
            "WS unregister user=%s remaining=%d",
            conn.user_id, len(self._conns.get(conn.user_id, [])),
        )

    # ── Sending ──────────────────────────────────────────────────────────
    async def _writer_loop(self, conn: _Connection) -> None:
        """Drain the per-connection outbox and write to the wire.
        One coroutine per connection — clean serialisation, no torn frames.
        Also sends periodic heartbeats so dead connections die fast."""
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(
                        conn.outbox.get(), timeout=HEARTBEAT_INTERVAL_SECS,
                    )
                except asyncio.TimeoutError:
                    # No app messages in 25s — send a ping frame.
                    # If the socket is dead, send_text will raise and we
                    # exit the loop, which triggers the route's finally
                    # block to unregister.
                    try:
                        await conn.ws.send_text(json.dumps({"type": "ping"}))
                    except Exception:
                        return
                    continue

                try:
                    await conn.ws.send_text(json.dumps(msg))
                except Exception:
                    log.debug("WS send failed for user=%s; closing", conn.user_id)
                    return
        except asyncio.CancelledError:
            return

    def _enqueue(self, conn: _Connection, msg: Dict[str, Any]) -> None:
        """Put a message on conn.outbox, dropping oldest if full.
        Runs on conn.loop (scheduled via call_soon_threadsafe from publish)."""
        try:
            conn.outbox.put_nowait(msg)
        except asyncio.QueueFull:
            try:
                conn.outbox.get_nowait()
                conn.outbox.put_nowait(msg)
            except Exception:
                log.warning("WS outbox full; dropped event for user=%s", conn.user_id)

    # ── Public publish (sync-safe) ────────────────────────────────────────
    def publish(self, user_id: int, event: Dict[str, Any]) -> int:
        """Fan out an event to every connection of a user.
        Thread-safe — usable from any sync code (FastAPI threadpool,
        SQLAlchemy after_commit listener, background task)."""
        conns = list(self._conns.get(user_id, []))
        scheduled = 0
        for conn in conns:
            try:
                conn.loop.call_soon_threadsafe(self._enqueue, conn, event)
                scheduled += 1
            except RuntimeError:
                # Loop is closed — server shutting down. Skip silently.
                continue
        return scheduled

    def publish_many(self, user_ids: List[int], event: Dict[str, Any]) -> int:
        """Convenience for broadcasting to multiple users in one call.
        Used for case-link events that notify two investigators."""
        total = 0
        for uid in set(user_ids):
            total += self.publish(uid, event)
        return total

    def connected_users(self) -> List[int]:
        """For admin/debug — who's online right now."""
        return list(self._conns.keys())


# ── Module-level singleton ───────────────────────────────────────────────
# Everything imports this — there is exactly one manager per process.
manager = WebSocketManager()


# ─────────────────────────────────────────────────────────────────────────────
# Compatibility shim
# ─────────────────────────────────────────────────────────────────────────────
# `notification_service.push` currently imports `publish` from
# `services.notification_events`. We re-export the same name here so the
# import line in notification_service.py only needs a one-token change.
def publish(user_id: int, event: Dict[str, Any]) -> int:
    payload = {"type": "notification", **event}
    return manager.publish(user_id, payload)