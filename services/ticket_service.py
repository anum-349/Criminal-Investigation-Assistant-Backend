# services/ticket_service.py
import secrets
from datetime import UTC, datetime
from typing import Optional, List

from sqlalchemy.orm import Session, joinedload
from sqlalchemy import desc, or_
from fastapi import HTTPException, Request

from models import Ticket, TicketReply, TicketStatus, User, Notification, Severity
from schemas.ticket_schema import (
    CreateTicketRequest, UpdateTicketRequest,
    AddReplyRequest, TicketOut, TicketReplyOut, TicketListOut,
)
from services import audit_service as audit
import logging

log = logging.getLogger(__name__)


# ── helpers ───────────────────────────────────────────────────────────────

def _ticket_status(db: Session, code: str) -> TicketStatus:
    row = db.query(TicketStatus).filter(TicketStatus.code == code).first()
    if not row:
        raise HTTPException(status_code=500, detail=f"Ticket status '{code}' missing from DB")
    return row


def _severity_id(db: Session, label: str) -> Optional[int]:
    row = db.query(Severity).filter(Severity.label == label).first()
    return row.id if row else None


def _gen_ticket_id(db: Session) -> str:
    date_part = datetime.now(UTC).strftime("%Y%m%d")
    prefix    = f"TKT-{date_part}-"
    count = db.query(Ticket).filter(Ticket.ticket_id.like(f"{prefix}%")).count()
    return f"{prefix}{count + 1:03d}"


def _reply_out(r: TicketReply) -> TicketReplyOut:
    return TicketReplyOut(
        id         = r.id,
        author_id  = r.author_id,
        author_name= r.author.username if r.author else None,
        body       = r.body,
        is_admin   = r.is_admin,
        created_at = r.created_at,
    )


def _ticket_out(t: Ticket) -> TicketOut:
    return TicketOut(
        id               = t.id,
        ticket_id        = t.ticket_id,
        sender_id        = t.sender_id,
        sender_name      = t.sender.username if t.sender else None,
        priority         = t.priority,
        subject          = t.subject,
        message          = t.message,
        status           = t.status.label if t.status else "—",
        status_code      = t.status.code  if t.status else "—",
        assigned_to_id   = t.assigned_to_id,
        assigned_to_name = t.assigned_to.username if t.assigned_to else None,
        admin_notes      = t.admin_notes,
        resolved_at      = t.resolved_at,
        created_at       = t.created_at,
        updated_at       = t.updated_at,
        replies          = [_reply_out(r) for r in (t.replies or [])],
    )


def _notify_admins(db: Session, *, ticket: Ticket, title: str, message: str):
    """Push a notification to every active admin."""
    admins = db.query(User).filter(User.role == "admin", User.status == "active").all()
    now    = datetime.now(UTC)
    sev_id = _severity_id(db, "Normal")
    for admin in admins:
        db.add(Notification(
            notification_id = f"NOTIF-TKT-{secrets.token_hex(6).upper()}",
            user_id         = admin.id,
            type            = "TICKET",
            severity_id     = sev_id,
            title           = title,
            message         = message,
            created_at      = now,
        ))


def _notify_sender(db: Session, *, ticket: Ticket, title: str, message: str):
    """Notify the original ticket sender (e.g. admin replied)."""
    sev_id = _severity_id(db, "Normal")
    db.add(Notification(
        notification_id = f"NOTIF-TKT-{secrets.token_hex(6).upper()}",
        user_id         = ticket.sender_id,
        type            = "TICKET",
        severity_id     = sev_id,
        title           = title,
        message         = message,
        created_at      = datetime.now(UTC),
    ))


# ── public API ────────────────────────────────────────────────────────────

def create_ticket(
    db: Session, *, user: User,
    body: CreateTicketRequest,
    request: Optional[Request],
) -> TicketOut:
    status = _ticket_status(db, "OPEN")
    ticket = Ticket(
        ticket_id  = _gen_ticket_id(db),
        sender_id  = user.id,
        priority   = body.priority,
        subject    = body.subject,
        message    = body.message,
        status_id  = status.id,
        created_at = datetime.now(UTC),
        updated_at = datetime.now(UTC),
    )
    db.add(ticket)
    try:
        db.flush()
        _notify_admins(db, ticket=ticket,
            title   = f"New Ticket [{body.priority.upper()}]: {body.subject}",
            message = f"From {user.username}: {body.message[:120]}",
        )
        audit.log_event(
            db, user_id=user.id, action="CREATE", module="Tickets",
            detail  = f"Ticket {ticket.ticket_id} created — {body.subject}",
            target_type="ticket", target_id=ticket.ticket_id, request=request,
        )
        db.commit()
    except Exception:
        db.rollback()
        log.exception("create_ticket failed")
        raise HTTPException(status_code=500, detail="Failed to create ticket")

    fresh = _load_ticket(db, ticket.id)
    return _ticket_out(fresh)


def list_tickets(
    db: Session, *, user: User,
    request: Optional[Request],
    page: int = 1, page_size: int = 10,
    status_filter: str = "all",
    search: str = "",
) -> TicketListOut:
    """Admins see all tickets; investigators see only their own."""
    q = (
        db.query(Ticket)
        .options(
            joinedload(Ticket.sender),
            joinedload(Ticket.assigned_to),
            joinedload(Ticket.status),
            joinedload(Ticket.replies).joinedload(TicketReply.author),
        )
    )

    if user.role != "admin":
        q = q.filter(Ticket.sender_id == user.id)

    if status_filter.upper() != "ALL":
        q = q.join(TicketStatus, Ticket.status_id == TicketStatus.id) \
             .filter(TicketStatus.code == status_filter.upper())

    s = search.strip()
    if s:
        like = f"%{s}%"
        q = q.filter(or_(
            Ticket.subject.ilike(like),
            Ticket.message.ilike(like),
            Ticket.ticket_id.ilike(like),
        ))

    total = q.distinct().count()
    page  = max(1, page)
    rows  = (
        q.order_by(desc(Ticket.created_at))
         .distinct()
         .limit(page_size)
         .offset((page - 1) * page_size)
         .all()
    )
    return TicketListOut(
        items     = [_ticket_out(t) for t in rows],
        total     = total,
        page      = page,
        page_size = page_size,
    )


def get_ticket(
    db: Session, *, user: User, ticket_id: str,
    request: Optional[Request],
) -> TicketOut:
    t = _load_ticket_by_str(db, ticket_id)
    if user.role != "admin" and t.sender_id != user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    return _ticket_out(t)


def update_ticket(
    db: Session, *, user: User, ticket_id: str,
    body: UpdateTicketRequest,
    request: Optional[Request],
) -> TicketOut:
    """Admin only — change status / assign / add internal notes."""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    t = _load_ticket_by_str(db, ticket_id)
    changes = []

    if body.status:
        new_status = _ticket_status(db, body.status.upper())
        if new_status.id != t.status_id:
            t.status_id = new_status.id
            changes.append(f"status → {new_status.label}")
            if new_status.code in ("RESOLVED", "CLOSED"):
                t.resolved_at = datetime.now(UTC)
            # Notify sender
            _notify_sender(db, ticket=t,
                title   = f"Your ticket has been {new_status.label.lower()}",
                message = f"Ticket {t.ticket_id}: {t.subject}",
            )

    if body.assigned_to is not None:
        t.assigned_to_id = body.assigned_to
        changes.append(f"assigned_to → {body.assigned_to}")

    if body.admin_notes is not None:
        t.admin_notes = body.admin_notes
        changes.append("admin_notes updated")

    t.updated_at = datetime.now(UTC)

    if changes:
        try:
            audit.log_event(
                db, user_id=user.id, action="UPDATE", module="Tickets",
                detail      = f"Ticket {ticket_id}: {', '.join(changes)}",
                target_type = "ticket", target_id=ticket_id, request=request,
            )
            db.commit()
        except Exception:
            db.rollback()
            log.exception("update_ticket failed")
            raise HTTPException(status_code=500, detail="Update failed")

    return _ticket_out(_load_ticket_by_str(db, ticket_id))


def add_reply(
    db: Session, *, user: User, ticket_id: str,
    body: AddReplyRequest,
    request: Optional[Request],
) -> TicketOut:
    t = _load_ticket_by_str(db, ticket_id)

    # Only the sender or any admin may reply
    if user.role != "admin" and t.sender_id != user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    is_admin = user.role == "admin"
    reply = TicketReply(
        ticket_id  = t.id,
        author_id  = user.id,
        body       = body.body,
        is_admin   = is_admin,
        created_at = datetime.now(UTC),
    )
    db.add(reply)

    # Auto-move to IN_PROGRESS when admin first replies
    if is_admin:
        in_progress = _ticket_status(db, "IN_PROGRESS")
        open_status = _ticket_status(db, "OPEN")
        if t.status_id == open_status.id:
            t.status_id  = in_progress.id
            t.updated_at = datetime.now(UTC)
        _notify_sender(db, ticket=t,
            title   = f"Admin replied to your ticket: {t.subject}",
            message = body.body[:120],
        )
    else:
        _notify_admins(db, ticket=t,
            title   = f"Sender replied to ticket {t.ticket_id}",
            message = body.body[:120],
        )

    try:
        audit.log_event(
            db, user_id=user.id, action="UPDATE", module="Tickets",
            detail      = f"Reply added to ticket {ticket_id}",
            target_type = "ticket", target_id=ticket_id, request=request,
        )
        db.commit()
    except Exception:
        db.rollback()
        log.exception("add_reply failed")
        raise HTTPException(status_code=500, detail="Reply failed")

    return _ticket_out(_load_ticket_by_str(db, ticket_id))


def delete_ticket(
    db: Session, *, user: User, ticket_id: str,
    request: Optional[Request],
) -> dict:
    """Admin or the original sender can delete (soft-close is preferred)."""
    t = _load_ticket_by_str(db, ticket_id)
    if user.role != "admin" and t.sender_id != user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    db.delete(t)
    try:
        audit.log_event(
            db, user_id=user.id, action="DELETE", module="Tickets",
            detail      = f"Ticket {ticket_id} deleted",
            target_type = "ticket", target_id=ticket_id, request=request,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Delete failed")
    return {"deleted": True, "ticket_id": ticket_id}


# ── private loaders ───────────────────────────────────────────────────────

def _load_ticket(db: Session, pk: int) -> Ticket:
    t = (
        db.query(Ticket)
        .options(
            joinedload(Ticket.sender),
            joinedload(Ticket.assigned_to),
            joinedload(Ticket.status),
            joinedload(Ticket.replies).joinedload(TicketReply.author),
        )
        .filter(Ticket.id == pk)
        .first()
    )
    if not t:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return t


def _load_ticket_by_str(db: Session, ticket_id: str) -> Ticket:
    t = (
        db.query(Ticket)
        .options(
            joinedload(Ticket.sender),
            joinedload(Ticket.assigned_to),
            joinedload(Ticket.status),
            joinedload(Ticket.replies).joinedload(TicketReply.author),
        )
        .filter(Ticket.ticket_id == ticket_id)
        .first()
    )
    if not t:
        raise HTTPException(status_code=404, detail=f"Ticket '{ticket_id}' not found")
    return t