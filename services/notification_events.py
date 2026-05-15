# services/notification_events.py
"""
In-process pub/sub for notification events.

Why this exists
───────────────
Replaces the 30-second poller in the frontend Header with a server-push
model. The frontend opens ONE long-lived SSE connection and the backend
pushes a tiny event over it whenever a notification is created for the
authenticated user. The frontend then refetches via the existing
/api/notifications endpoint — so this module never has to serialize
notification rows, only signal "you have new data."

Thread safety
─────────────
asyncio.Queue is NOT thread-safe. The SSE coroutine waits on it in the
main event loop; FastAPI sync route handlers run in a threadpool worker
and would otherwise corrupt the queue (or fail to wake the waiter).

We solve this by capturing the event loop when each queue is created
(during subscribe) and scheduling put_nowait via loop.call_soon_threadsafe.
That works whether publish is called from the main loop or a worker
thread, with no awaiting.
"""

import asyncio
from collections import defaultdict
from typing import Any, Dict, List, Tuple
import logging

log = logging.getLogger(__name__)

# user_id → list of (queue, loop) pairs.
# Capturing the loop alongside the queue lets us cross thread boundaries
# safely when publishing from a sync route handler running in the threadpool.
_subscribers: Dict[int, List[Tuple[asyncio.Queue, asyncio.AbstractEventLoop]]] = defaultdict(list)

# Per-queue buffer. Small on purpose — clients refetch on any event, so
# we don't need long histories.
_QUEUE_MAXSIZE = 16


def subscribe(user_id: int) -> asyncio.Queue:
    """Register a new SSE connection. Caller is responsible for calling
    `unsubscribe` in a finally block when the connection closes.

    Must be called from an async context (inside the SSE coroutine) so
    asyncio.get_running_loop() returns the live event loop we'll use
    later for cross-thread scheduling.
    """
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
    _subscribers[user_id].append((q, loop))
    log.debug("SSE subscribe user=%s total_conns=%d", user_id, len(_subscribers[user_id]))
    return q


def unsubscribe(user_id: int, q: asyncio.Queue) -> None:
    """Drop a queue when its SSE connection closes."""
    pairs = _subscribers.get(user_id, [])
    _subscribers[user_id] = [(qq, ll) for (qq, ll) in pairs if qq is not q]
    if not _subscribers[user_id]:
        _subscribers.pop(user_id, None)
    log.debug("SSE unsubscribe user=%s remaining_conns=%d",
              user_id, len(_subscribers.get(user_id, [])))


def _put_on_loop(q: asyncio.Queue, event: Dict[str, Any]) -> None:
    """Helper that runs ON the queue's owning event loop.
    Safe to do put_nowait here because we're guaranteed to be in the
    right thread for this queue.
    """
    try:
        q.put_nowait(event)
    except asyncio.QueueFull:
        # Drop OLDEST event and try again. Losing one event is fine
        # because the next event triggers a full refetch anyway.
        try:
            q.get_nowait()
            q.put_nowait(event)
        except Exception:
            log.warning("SSE queue full; dropped event")


def publish(user_id: int, event: Dict[str, Any]) -> int:
    """Fan-out an event to every open SSE connection for `user_id`.

    Thread-safe: can be called from FastAPI's threadpool, from inside
    SQLAlchemy after_commit listeners, or from the main event loop.

    Returns the number of queues the event was scheduled on (not yet
    necessarily delivered — call_soon_threadsafe is async).
    """
    pairs = _subscribers.get(user_id, [])
    scheduled = 0
    for q, loop in pairs:
        try:
            # call_soon_threadsafe IS the thread-safe entry point for
            # asyncio. It works whether we're already on `loop`'s thread
            # or in a worker thread.
            loop.call_soon_threadsafe(_put_on_loop, q, event)
            scheduled += 1
        except RuntimeError:
            # Loop is closed (server shutting down). Skip.
            log.debug("SSE publish: loop closed for user=%s", user_id)
    return scheduled