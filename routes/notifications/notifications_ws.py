from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from models import User
from dependencies.auth import resolve_user_from_token
from services.realtime.ws_manager import manager

log = logging.getLogger(__name__)

router = APIRouter()

WS_CLOSE_AUTH_MISSING = 4401
WS_CLOSE_AUTH_INVALID = 4403
WS_CLOSE_USER_NOT_FOUND = 4404
 
 
@router.websocket("/ws")
async def notifications_ws(
    websocket: WebSocket,
    token: Optional[str] = Query(default=None),
) -> None:
    """Long-lived WebSocket for pushing notifications to one user."""
    user = resolve_user_from_token(token)
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
