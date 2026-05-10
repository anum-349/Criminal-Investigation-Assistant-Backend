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
from services.service_helper import _format_officer_name, _parse_ymd, _resolve_case, _ymd

EVENT_SOURCE_SYSTEM = "SYSTEM"
EVENT_SOURCE_MANUAL = "MANUAL"
EVENT_SOURCE_AI     = "AI"

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
        system=sum(1 for r in items if r.eventSource in ("system", "ai")),
    )

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

    if not body.eventType or not body.eventType.strip():
        raise HTTPException(status_code=400, detail="Event type is required.")
    if not body.title or not body.title.strip():
        raise HTTPException(status_code=400, detail="Title is required.")
    if not (body.description or "").strip():
        raise HTTPException(status_code=400, detail="Description is required.")

    event_date = _parse_ymd(body.date) or datetime.now(UTC).date()

    follow_up_date = _parse_ymd(body.followUpDate) if body.followUpRequired else None
    if body.followUpRequired and not follow_up_date:
        raise HTTPException(
            status_code=400,
            detail="Follow-up date is required when 'Follow-up Required' is checked.",
        )

    officer = (body.officerName or "").strip() or _format_officer_name(user)

    type_id = _get_or_create_manual_event_type(db, body.eventType.strip())
    severity_id = _severity_id_by_label(db, body.severity or "Normal")

    now = datetime.now(UTC)
    ev = TimelineEvent(
        case_id_fk=case.id,
        event_id=_next_event_id(case.case_id, count= len(case.timeline_events)),
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

        db.add(Activity(
            title=ev.title,
            description=ev.description or "",
            type=MANUAL_TYPE_TO_ACTIVITY.get(body.eventType, "investigation"),
            case_id=case.id,
            user_id=user.id,
            created_at=now,
        ))
        db.flush()

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