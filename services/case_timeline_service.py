"""
services/case_timeline_service.py
─────────────────────────────────────────────────────────────────────────────
Powers the case-detail "Timeline" tab (src/pages/investigator/case/[id]/CaseTimeline.jsx).

Public functions:
  • list_timeline()         — GET (full timeline + counts)
  • add_manual_event()      — POST (one manual event)
  • delete_manual_event()   — DELETE (manual events only)

Auto-logged events (SYSTEM / AI)
────────────────────────────────
Auto-logged events are NOT created here. They are emitted by the triple-write
helpers in:
  • services/case_detail_service.py     (suspects, victims, witnesses, evidences)
  • services/case_evidence_service.py   (photo upload/delete, evidence updates)
  • services/case_suspect_service.py    (suspect updates)
  • services/case_lead_service.py       (manual & AI leads, lead status changes)

This service only needs to:
  - LIST them (alongside manual events) so the Timeline tab can render both
  - NEVER let the user delete them (editable=false enforced server-side)

Manual events
─────────────
Created here from AddTimelineDialog. We still emit ONE row per call:
  • timeline_events  (the row itself, event_source="MANUAL", editable=True)
  • activities       (so the dashboard recent feed picks it up)
  • audit_logs       (R3.2.1.1.5)
…all in a single transaction.
"""

import secrets
from datetime import UTC, datetime, date
from typing import Optional

from sqlalchemy import desc
from sqlalchemy.orm import Session, joinedload
from fastapi import HTTPException, Request

from models import (
    User,
    Case,
    Activity,
    Severity,
    TimelineEvent, TimelineEventType,
)
from services import audit_service as audit
from schemas.case_timeline_schema import (
    TimelineEventRow, TimelineCounts, CaseTimelineList,
    AddTimelineEventRequest, DeleteTimelineEventResult,
)


# ─── Constants ──────────────────────────────────────────────────────────────

EVENT_SOURCE_SYSTEM = "SYSTEM"
EVENT_SOURCE_MANUAL = "MANUAL"
EVENT_SOURCE_AI     = "AI"

# Mapping of MANUAL_EVENT_TYPES (frontend labels) → suggested code values for
# the lkp_timeline_event_types row (in case the row has to be auto-created).
# Codes use SCREAMING_SNAKE_CASE to match the existing system codes.
MANUAL_LABEL_TO_CODE = {
    "Field Visit":         "FIELD_VISIT",
    "Witness Interview":   "WITNESS_INTERVIEW",
    "Suspect Interview":   "SUSPECT_INTERVIEW",
    "Arrest":              "ARREST",
    "Evidence Collection": "EVIDENCE_COLLECTION",
    "Court Hearing":       "COURT_HEARING",
    "Surveillance":        "SURVEILLANCE",
    "Informant Contact":   "INFORMANT_CONTACT",
    "Forensic Visit":      "FORENSIC_VISIT",
    "Status Update":       "STATUS_UPDATE",
    "Note / Observation":  "NOTE_OBSERVATION",
    "Other":               "OTHER",
}

# Activity row colour mapping for manual events.
MANUAL_TYPE_TO_ACTIVITY = {
    "Field Visit":         "investigation",
    "Witness Interview":   "investigation",
    "Suspect Interview":   "investigation",
    "Arrest":              "investigation",
    "Evidence Collection": "investigation",
    "Court Hearing":       "update",
    "Surveillance":        "investigation",
    "Informant Contact":   "investigation",
    "Forensic Visit":      "investigation",
    "Status Update":       "update",
    "Note / Observation":  "update",
    "Other":               "update",
}


# ─── Helpers ────────────────────────────────────────────────────────────────

def _resolve_case(db: Session, *, user: User, case_id: str) -> Case:
    case = (
        db.query(Case)
        .filter(Case.case_id == case_id, Case.is_deleted == False)  # noqa: E712
        .first()
    )
    if not case:
        raise HTTPException(status_code=404, detail=f"Case '{case_id}' not found")
    if user.role != "admin" and case.assigned_investigator_id != user.id:
        raise HTTPException(status_code=403, detail="You don't have access to this case")
    return case


def _resolve_event(db: Session, *, case: Case, event_id: str) -> TimelineEvent:
    ev = (
        db.query(TimelineEvent)
        .filter(
            TimelineEvent.case_id_fk == case.id,
            TimelineEvent.event_id == event_id,
        )
        .options(joinedload(TimelineEvent.event_type), joinedload(TimelineEvent.severity))
        .first()
    )
    if not ev:
        raise HTTPException(status_code=404, detail=f"Timeline event '{event_id}' not found")
    return ev


def _format_officer_name(user: User) -> str:
    rank = ""
    if user.investigator and user.investigator.rank:
        rank = f"{user.investigator.rank}. "
    return f"{rank}{user.username}"


def _next_event_id(case_id: str, count: int) -> str:
    short_case_id = case_id.split("-")[-1]
    return f"EV-{short_case_id}-T{count + 1:02d}"


def _severity_id_by_label(db: Session, label: Optional[str]) -> Optional[int]:
    if not label:
        return None
    row = db.query(Severity).filter(Severity.label == label).first()
    return row.id if row else None


def _get_or_create_manual_event_type(db: Session, label: str) -> int:
    """
    Manual event types are not part of the closed SYSTEM_EVENT set. If the
    investigator picks 'Field Visit' from the dropdown but the lookup row
    doesn't exist yet (fresh DB, partial seed, etc.), create it on the fly
    so the insert never fails.
    """
    row = db.query(TimelineEventType).filter(TimelineEventType.label == label).first()
    if row:
        return row.id

    code = MANUAL_LABEL_TO_CODE.get(label, label.upper().replace(" ", "_").replace("/", "_"))

    # Avoid colliding on code if it exists with a different label
    by_code = db.query(TimelineEventType).filter(TimelineEventType.code == code).first()
    if by_code:
        return by_code.id

    new_row = TimelineEventType(
        code=code,
        label=label,
        is_system=False,
        sort_order=100,
    )
    db.add(new_row)
    db.flush()
    return new_row.id


def _ymd(d) -> str:
    if not d:
        return ""
    if isinstance(d, datetime):
        return d.strftime("%Y-%m-%d")
    if isinstance(d, date):
        return d.strftime("%Y-%m-%d")
    if hasattr(d, "strftime"):
        return d.strftime("%Y-%m-%d")
    return ""


def _parse_ymd(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _classify_source(ev: TimelineEvent) -> str:
    """
    Map ORM event_source → the lowercase string the frontend uses
    (EVENT_SOURCE in caseEventConstants.js).

    AI-generated rows are stored either with event_source='AI' (preferred)
    OR with event_source='SYSTEM' and event_type.code='AI_LEAD_GENERATED'.
    Both cases collapse to "ai" for the UI.
    """
    src = (ev.event_source or "").upper()
    code = (ev.event_type.code if ev.event_type else "") or ""

    if src == EVENT_SOURCE_AI or code == "AI_LEAD_GENERATED":
        return "ai"
    if src == EVENT_SOURCE_MANUAL:
        return "manual"
    return "system"


def _row_from_event(ev: TimelineEvent, case_id: str) -> TimelineEventRow:
    """Convert a TimelineEvent ORM row to the response shape."""
    return TimelineEventRow(
        id=ev.event_id,
        case_id=case_id,
        event_source=_classify_source(ev),
        event_type=ev.event_type.label if ev.event_type else (ev.event_source or ""),
        title=ev.title,
        description=ev.description,
        officer_name=ev.officer_name,
        severity=ev.severity.label if ev.severity else "Normal",
        location=ev.location,
        outcome=ev.outcome,
        attachment_note=ev.attachment_note,
        follow_up_required=bool(ev.follow_up_required),
        follow_up_date=_ymd(ev.follow_up_date) or None,
        date=_ymd(ev.event_date),
        time=ev.event_time,
        created_at=ev.created_at,
        editable=bool(ev.editable),
    )


# ─── 1. List ────────────────────────────────────────────────────────────────

def list_timeline(
    db: Session,
    *,
    user: User,
    case_id: str,
    request: Optional[Request],
) -> CaseTimelineList:
    """Return EVERY timeline row for this case (newest first), plus counts."""
    case = _resolve_case(db, user=user, case_id=case_id)

    rows = (
        db.query(TimelineEvent)
        .filter(TimelineEvent.case_id_fk == case.id)
        .options(
            joinedload(TimelineEvent.event_type),
            joinedload(TimelineEvent.severity),
        )
        .order_by(desc(TimelineEvent.created_at))
        .all()
    )

    items = [_row_from_event(ev, case.case_id) for ev in rows]

    counts = TimelineCounts(
        all=len(items),
        manual=sum(1 for r in items if r.eventSource == "manual"),
        ai=sum(1 for r in items if r.eventSource == "ai"),
        # "system" in the count includes AI rows because the UI's left column
        # (System & AI) shows both.
        system=sum(1 for r in items if r.eventSource in ("system", "ai")),
    )

    # Audit (best-effort)
    try:
        audit.log_event(
            db, user_id=user.id, action="VIEW", module="Case Management",
            detail=(
                f"Viewed timeline for case '{case_id}'. "
                f"Returned {counts.all} events "
                f"({counts.system} auto-logged, {counts.manual} manual)."
            ),
            target_type="timeline_list", target_id=case_id, request=request,
        )
        db.commit()
    except Exception:
        db.rollback()

    return CaseTimelineList(items=items, counts=counts)


# ─── 2. Add manual event ────────────────────────────────────────────────────

def add_manual_event(
    db: Session,
    *,
    user: User,
    case_id: str,
    body: AddTimelineEventRequest,
    request: Optional[Request],
) -> TimelineEventRow:
    """Insert a single manual TimelineEvent + Activity + AuditLog."""
    case = _resolve_case(db, user=user, case_id=case_id)

    # Required-ish validation (the frontend already enforces these, but the
    # backend is the source of truth — never trust the client alone).
    if not body.eventType or not body.eventType.strip():
        raise HTTPException(status_code=400, detail="Event type is required.")
    if not body.title or not body.title.strip():
        raise HTTPException(status_code=400, detail="Title is required.")
    if not (body.description or "").strip():
        raise HTTPException(status_code=400, detail="Description is required.")

    # Event date — default to today if missing/invalid
    event_date = _parse_ymd(body.date) or datetime.utcnow().date()

    # Follow-up sanity: if checked, date is required
    follow_up_date = _parse_ymd(body.followUpDate) if body.followUpRequired else None
    if body.followUpRequired and not follow_up_date:
        raise HTTPException(
            status_code=400,
            detail="Follow-up date is required when 'Follow-up Required' is checked.",
        )

    # Officer name — fall back to the caller's display name
    officer = (body.officerName or "").strip() or _format_officer_name(user)

    # Resolve / create the event-type row
    type_id = _get_or_create_manual_event_type(db, body.eventType.strip())
    severity_id = _severity_id_by_label(db, body.severity or "Normal")

    now = datetime.now(UTC)
    ev = TimelineEvent(
        case_id_fk=case.id,
        event_id=_next_event_id(case.case_id, count= 0),
        event_source=EVENT_SOURCE_MANUAL,
        event_type_id=type_id,
        title=body.title.strip()[:255],
        description=(body.description or "").strip() or None,
        officer_name=officer[:150],
        severity_id=severity_id,
        location=(body.location or "").strip()[:255] or None,
        outcome=(body.outcome or "").strip() or None,
        attachment_note=(body.attachmentNote or "").strip() or None,
        follow_up_required=bool(body.followUpRequired),
        follow_up_date=follow_up_date,
        event_date=event_date,
        event_time=(body.time or now.strftime("%H:%M"))[:10],
        created_at=now,
        editable=True,
    )

    try:
        db.add(ev)

        # Activity (drives dashboard recent feed)
        db.add(Activity(
            title=ev.title,
            description=ev.description or "",
            type=MANUAL_TYPE_TO_ACTIVITY.get(body.eventType, "investigation"),
            case_id=case.id,
            user_id=user.id,
            created_at=now,
        ))
        db.flush()

        # Audit
        try:
            audit.log_event(
                db, user_id=user.id, action="CREATE", module="Case Management",
                detail=f"Manual timeline event added: '{ev.title}' (case {case.case_id})",
                target_type="timeline_event", target_id=ev.event_id, request=request,
            )
        except Exception:
            pass

        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to add timeline event: {e}")

    db.refresh(ev)
    fresh = _resolve_event(db, case=case, event_id=ev.event_id)  # re-load joined rels
    return _row_from_event(fresh, case.case_id)


# ─── 3. Delete manual event ─────────────────────────────────────────────────

def delete_manual_event(
    db: Session,
    *,
    user: User,
    case_id: str,
    event_id: str,
    request: Optional[Request],
) -> DeleteTimelineEventResult:
    """Hard-delete a MANUAL timeline event. System / AI events are immutable."""
    case = _resolve_case(db, user=user, case_id=case_id)
    ev = _resolve_event(db, case=case, event_id=event_id)

    src = (ev.event_source or "").upper()
    if src != EVENT_SOURCE_MANUAL or not ev.editable:
        raise HTTPException(
            status_code=400,
            detail=(
                "Auto-logged events (system / AI) cannot be deleted — they are "
                "preserved as part of the case audit trail."
            ),
        )

    title = ev.title

    try:
        db.delete(ev)

        try:
            audit.log_event(
                db, user_id=user.id, action="DELETE", module="Case Management",
                detail=f"Manual timeline event deleted: '{title}' (id={event_id}, case {case.case_id})",
                target_type="timeline_event", target_id=event_id, request=request,
            )
        except Exception:
            pass

        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to delete timeline event: {e}")

    return DeleteTimelineEventResult(deleted_id=event_id)