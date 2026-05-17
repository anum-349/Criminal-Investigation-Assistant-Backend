from __future__ import annotations

import json
import logging
import os
from typing import Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from jose import JWTError, jwt
from sqlalchemy.orm import Session

# Adjust these imports to match your project's auth module layout.
from db import SessionLocal
from models import User
from services.realtime.ws_manager import manager

log = logging.getLogger(__name__)

router = APIRouter()

JWT_SECRET = os.getenv("JWT_SECRET")
JWT_ALGORITHM = "HS256"

WS_CLOSE_AUTH_MISSING = 4401
WS_CLOSE_AUTH_INVALID = 4403
WS_CLOSE_USER_NOT_FOUND = 4404
 
 
def _resolve_user_from_token(token: Optional[str]) -> Optional[User]:
    """Decode JWT and return the User, or None on any failure.
 
    Accepts any of these claim names for the user ID, in priority order:
      • id        (what THIS app issues — see auth/jwt.py)
      • sub       (RFC 7519 standard)
      • user_id   (legacy fallback)
    """
    if not token:
        log.debug("WS auth: no token in query string")
        return None
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError as e:
        log.debug("WS auth: JWT decode failed: %s", e)
        return None
 
    # Order matters — this app uses "id", so check it first.
    user_id = (
        payload.get("id")
        or payload.get("sub")
        or payload.get("user_id")
    )
    if user_id is None:
        log.debug("WS auth: no user-id claim in token, claims=%s", list(payload.keys()))
        return None
    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        log.debug("WS auth: non-int user-id claim: %r", user_id)
        return None
 
    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if user is None:
            log.debug("WS auth: user_id=%s not found in DB", user_id)
        return user
    finally:
        db.close()
 
 
@router.websocket("/ws")
async def notifications_ws(
    websocket: WebSocket,
    token: Optional[str] = Query(default=None),
) -> None:
    """Long-lived WebSocket for pushing notifications to one user."""
    user = _resolve_user_from_token(token)
    if user is None:
        await websocket.accept()
        await websocket.close(code=WS_CLOSE_AUTH_INVALID)
        return
 
    await websocket.accept()
    conn = await manager.register(websocket, user_id=user.id)
    log.info("WS connected: user_id=%s username=%s", user.id, user.username)
 
    try:
        await websocket.send_text(json.dumps({
            "type": "ready",
            "user_id": user.id,
        }))
    except Exception:
        await manager.unregister(conn)
        return
 
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            mtype = msg.get("type")
            if mtype in ("pong", "ack"):
                continue
    except WebSocketDisconnect:
        log.debug("WS disconnect user_id=%s", user.id)
    except Exception:
        log.exception("WS unexpected error user_id=%s", user.id)
    finally:
        await manager.unregister(conn)