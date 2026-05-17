import secrets
from datetime import UTC, datetime
from typing import Optional, List

from sqlalchemy.orm import Session
from sqlalchemy import desc
from fastapi import HTTPException, Request
from sqlalchemy import event as sa_event

from models import Notification, User, UserPreference, Severity
import logging

# ─── Transport swap: SSE → WebSocket ──────────────────────────────────────
# Old: `from services.notification_events import publish as _publish_notification_event`
# The new module exposes a `publish` function with the same shape; the only
# difference is the events go over a WebSocket instead of an SSE stream.

log = logging.getLogger(__name__)

# ── Preference keys ────────────────────────────────────────────────────────
PREF_KEYS = [
    "case_update_alerts",
    "ai_lead_notifications",
    "case_link_alerts",
    "sound_alerts",
]

PREF_KEY_MAP = {
    "CASE_UPDATE":   "case_update_alerts",
    "CASE_ASSIGNED": "case_update_alerts",
    "NEW_LEAD":      "ai_lead_notifications",
    "CASE_LINKED":   "case_link_alerts",
}

# ── helpers ────────────────────────────────────────────────────────────────

def _notif_id() -> str:
    return f"NOTIF-{secrets.token_hex(6).upper()}"
 
 
def _sev_id(db: Session, label: str) -> Optional[int]:
    from models import Severity
    row = db.query(Severity).filter(Severity.label == label).first()
    return row.id if row else None
 
 
def _pref_enabled(db: Session, user_id: int, key: str) -> bool:
    """Returns True if the preference is enabled (default True if not set)."""
    row = db.query(UserPreference).filter(
        UserPreference.user_id  == user_id,
        UserPreference.pref_key == key,
    ).first()
    if not row:
        return True   # default ON
    return row.pref_value.lower() not in ("false", "0", "no", "off")
 
 
def _row_to_dict(n: Notification) -> dict:
    return {
        "id":           n.notification_id,
        "type":         n.type,
        "title":        n.title,
        "message":      n.message,
        "linkUrl":      n.link_url,
        "isRead":       n.is_read,
        "readAt":       n.read_at.isoformat() if n.read_at else None,
        "createdAt":    n.created_at.isoformat(),
        "severity":     n.severity.label if n.severity else None,
        "relatedCaseId": n.related_case_id,
    }

def _row_to_dict(n: Notification) -> dict:
    return {
        "id":           n.notification_id,
        "type":         n.type,
        "title":        n.title,
        "message":      n.message,
        "linkUrl":      n.link_url,
        "isRead":       n.is_read,
        "readAt":       n.read_at.isoformat() if n.read_at else None,
        "createdAt":    n.created_at.isoformat(),
        "severity":     n.severity.label if n.severity else None,
        "relatedCaseId": n.related_case_id,
    }


# ── public API ─────────────────────────────────────────────────────────────

def list_notifications(
    db: Session, *, user: User,
    page: int, page_size: int,
    unread_only: bool,
    request: Optional[Request],
) -> dict:
    q = (
        db.query(Notification)
        .filter(Notification.user_id == user.id)
        .order_by(desc(Notification.created_at))
    )
    if unread_only:
        q = q.filter(Notification.is_read == False)   # noqa: E712

    total = q.count()
    rows  = q.limit(page_size).offset((page - 1) * page_size).all()

    return {
        "items":      [_row_to_dict(n) for n in rows],
        "total":      total,
        "unreadCount": db.query(Notification).filter(
            Notification.user_id == user.id,
            Notification.is_read == False,   # noqa: E712
        ).count(),
        "page":       page,
        "page_size":  page_size,
    }


def mark_read(
    db: Session, *, user: User,
    notification_id: str,
    request: Optional[Request],
) -> dict:
    n = db.query(Notification).filter(
        Notification.notification_id == notification_id,
        Notification.user_id         == user.id,
    ).first()
    if not n:
        raise HTTPException(status_code=404, detail="Notification not found")
    n.is_read = True
    n.read_at = datetime.now(UTC)
    db.commit()
    return _row_to_dict(n)


def mark_all_read(
    db: Session, *, user: User, request: Optional[Request]
) -> dict:
    updated = (
        db.query(Notification)
        .filter(
            Notification.user_id == user.id,
            Notification.is_read == False,   # noqa: E712
        )
        .all()
    )
    now = datetime.now(UTC)
    for n in updated:
        n.is_read = True
        n.read_at = now
    db.commit()
    return {"marked": len(updated)}


def delete_notification(
    db: Session, *, user: User,
    notification_id: str,
    request: Optional[Request],
) -> dict:
    n = db.query(Notification).filter(
        Notification.notification_id == notification_id,
        Notification.user_id         == user.id,
    ).first()
    if not n:
        raise HTTPException(status_code=404, detail="Notification not found")
    db.delete(n)
    db.commit()
    return {"deleted": True, "id": notification_id}

def get_preferences(db: Session, *, user: User) -> dict:
    """Read only the 3 notification-relevant prefs from user_preferences."""
    rows = db.query(UserPreference).filter(
        UserPreference.user_id  == user.id,
        UserPreference.pref_key.in_(PREF_KEYS),
    ).all()

    # Build dict with DB values; default True for alerts, False for sound
    defaults = {
        "case_update_alerts":    False,
        "ai_lead_notifications": False,
        "sound_alerts":          False,
    }
    for row in rows:
        if row.pref_key in defaults:
            defaults[row.pref_key] = row.pref_value.lower() not in ("false","0","no","off")
    return defaults

def update_preferences(db: Session, *, user: User, prefs: dict) -> dict:
    """Upsert only the 3 notification prefs — ignore anything else."""
    now = datetime.now(UTC)
    for key in PREF_KEYS:
        if key not in prefs:
            continue
        str_val = "true" if prefs[key] else "false"
        row = db.query(UserPreference).filter(
            UserPreference.user_id  == user.id,
            UserPreference.pref_key == key,
        ).first()
        if row:
            row.pref_value = str_val
            row.updated_at = now
        else:
            db.add(UserPreference(
                user_id    = user.id,
                pref_key   = key,
                pref_value = str_val,
                updated_at = now,
            ))
    db.commit()
    return get_preferences(db, user=user)
# ── Push helper (call from other services) ────────────────────────────────


def push(
    db: Session, *,
    user_id:      int,
    type:         str,
    title:        str,
    message:      str  = "",
    link_url:     Optional[str] = None,
    related_case_id: Optional[int] = None,
    severity_label:  str = "Normal",
    pref_key:     Optional[str] = None,
) -> Optional[Notification]:
    """Create a Notification row, respecting user preferences.
 
    On commit, fires a WebSocket event to every connected client for this
    user (or a no-op if they're offline — the row still persists and they
    see it on next reconnect / refetch).
    """
    resolved_key = pref_key or PREF_KEY_MAP.get(type)
    if resolved_key and not _pref_enabled(db, user_id, resolved_key):
        return None
 
    n = Notification(
        notification_id = _notif_id(),
        user_id         = user_id,
        type            = type,
        severity_id     = _sev_id(db, severity_label),
        title           = title,
        message         = message[:500] if message else "",
        link_url        = link_url,
        related_case_id = related_case_id,
        is_read         = False,
        created_at      = datetime.now(UTC),
    )
    db.add(n)
 
    # ── Defer the WS publish until the caller commits ──────────────────
    # Same pattern as before: if the txn rolls back, no event fires.
    event_payload = {
        "type":     type,                  # notification.type, e.g. "CASE_LINKED"
        "title":    title,
        "severity": severity_label,
    }
    target_user_id = user_id
 
    def _on_commit(session):
        try:
            from services.realtime.ws_manager import publish
            print("data:", target_user_id, event_payload)
            publish(user_id = target_user_id, event=event_payload)
        except Exception:
            log.exception("Failed to publish WS event for user=%s", target_user_id)
 
    sa_event.listen(db, "after_commit", _on_commit, once=True)
 
    return n