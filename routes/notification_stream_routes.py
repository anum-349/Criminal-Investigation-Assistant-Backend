import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from auth.jwt import decode_access_token   # exists alongside create_access_token
from dependencies.auth import get_current_user
from models import User
from services.notification_events import subscribe, unsubscribe
from db import get_db   # adjust import if your project exposes it elsewhere
from services import notification_service as notif
from services.notification_events import _subscribers 

log = logging.getLogger(__name__)

router = APIRouter()

# How often to send a keepalive comment to defeat proxy idle-timeouts.
_HEARTBEAT_SECONDS = 25


def _resolve_user_from_token(token: str, db: Session) -> User:
    """Same logic our normal Depends(get_current_user) uses, but invoked
    explicitly here because EventSource can't send Authorization headers.

    Adjust the decode call / claim names to match auth/jwt.py if they
    differ — the token is created with {"id": user.id, "role": user.role}
    in user_service.login.
    """
    try:
        payload = decode_access_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")
    user_id = payload.get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    user = db.query(User).filter(User.id == user_id).first()
    if not user or user.status != "active":
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return user


@router.get("/stream")
async def notifications_stream(
    request: Request,
    token: str = Query(..., description="JWT access token (EventSource can't send headers)"),
    db: Session = Depends(get_db),
):
    """Open a long-lived SSE connection for the authenticated user."""
    user = _resolve_user_from_token(token, db)
    user_id = user.id

    queue = subscribe(user_id)

    async def event_generator():
        # Tell the client we're connected — the frontend treats this as
        # a cue to do an initial fetch (it would do one on mount anyway,
        # but emitting "ready" makes the contract explicit).
        yield "event: ready\ndata: {}\n\n"
        try:
            while True:
                # Disconnects are detected on the next read; we also
                # check explicitly so we don't keep pushing to a dead socket.
                if await request.is_disconnected():
                    break

                try:
                    event = await asyncio.wait_for(
                        queue.get(),
                        timeout=_HEARTBEAT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    # No event in window — send a comment line as a heartbeat.
                    # Lines starting with ":" are ignored by EventSource.
                    yield ": keepalive\n\n"
                    continue

                # Standard SSE framing:
                #   event: <name>
                #   data: <payload>
                #   <blank line>
                payload = json.dumps(event, separators=(",", ":"))
                yield f"event: notification\ndata: {payload}\n\n"
        except asyncio.CancelledError:
            # Client closed connection — normal path.
            raise
        finally:
            unsubscribe(user_id, queue)
            log.debug("SSE connection closed for user=%s", user_id)

    headers = {
        "Cache-Control": "no-cache, no-transform",
        "Content-Type": "text/event-stream",
        # Disable proxy buffering (nginx in particular).
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return StreamingResponse(event_generator(), headers=headers)
