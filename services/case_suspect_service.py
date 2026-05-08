import secrets
from datetime import UTC, datetime
from typing import Optional, List

from sqlalchemy import or_, desc
from sqlalchemy.orm import Session, joinedload
from fastapi import HTTPException, Request

from models import (
    User, Person, Case,
    CaseSuspect, SuspectStatus,
    Severity, Activity,
    TimelineEvent, TimelineEventType,
)
from services import audit_service as audit
from schemas.case_suspect_schema import (
    SuspectRow, CaseSuspectsList, UpdateSuspectRequest,
)

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


def _resolve_suspect(db: Session, *, case: Case, suspect_id: str) -> CaseSuspect:
    row = (
        db.query(CaseSuspect)
        .filter(
            CaseSuspect.case_id_fk == case.id,
            CaseSuspect.suspect_id == suspect_id,
        )
        .options(
            joinedload(CaseSuspect.person),
            joinedload(CaseSuspect.status),
            joinedload(CaseSuspect.case),
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Suspect '{suspect_id}' not found")
    return row


def _format_officer_name(user: User) -> str:
    rank = ""
    if user.investigator and user.investigator.rank:
        rank = f"{user.investigator.rank}. "
    return f"{rank}{user.username}"


def _suspect_status_id(db: Session, label: Optional[str]) -> Optional[int]:
    if not label:
        return None
    row = db.query(SuspectStatus).filter(SuspectStatus.label == label).first()
    return row.id if row else None


def _severity_id_by_label(db: Session, label: str) -> Optional[int]:
    row = db.query(Severity).filter(Severity.label == label).first()
    return row.id if row else None


def _timeline_event_type_id(db: Session, code: str) -> Optional[int]:
    row = db.query(TimelineEventType).filter(TimelineEventType.code == code).first()
    return row.id if row else None


def _ymd(d) -> str:
    if not d:
        return ""
    if isinstance(d, datetime):
        return d.strftime("%Y-%m-%d")
    return d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else ""


def _row_from_suspect(s: CaseSuspect) -> SuspectRow:
    p = s.person
    return SuspectRow(
        id=s.suspect_id,
        caseId=s.case.case_id if s.case else "",
        name=p.full_name if p else None,
        cnic=p.cnic if p else None,
        age=p.age if p else None,
        gender=p.gender if p else None,
        contact=p.contact if p else None,
        address=p.address if p else None,
        occupation=p.occupation if p else None,
        status=s.status.label if s.status else "—",
        relationToCase=s.relation_to_case,
        reason=s.reason,
        alibi=s.alibi,
        arrested=bool(s.arrested),
        criminalRecord=bool(s.criminal_record),
        dateAdded=_ymd(s.created_at) if hasattr(s, "created_at") and s.created_at else None,
        statementDate=None,
        physicalDescription=None,
        knownAffiliations=None,
        arrivalMethod=None,
        vehicleDescription=None,
        notes=None,
    )


def _log_suspect_action(
    db: Session, *,
    case: Case, user: User, request: Optional[Request],
    title: str, description: str, audit_action: str,
    audit_target_id: str,
):
    """Triple-write helper for one suspect mutation."""
    now = datetime.now(UTC)
    db.add(TimelineEvent(
        case_id_fk=case.id,
        event_id=f"EVT-{int(now.timestamp() * 1000):X}-{secrets.token_hex(2).upper()}",
        event_source="SYSTEM",
        event_type_id=_timeline_event_type_id(db, "SUSPECT_ADDED"),
        title=title,
        description=description,
        officer_name=_format_officer_name(user),
        severity_id=_severity_id_by_label(db, "Normal"),
        event_date=now.date(),
        event_time=now.strftime("%H:%M"),
        created_at=now,
        editable=False,
    ))
    db.add(Activity(
        title=title, description=description, type="investigation",
        case_id=case.id, user_id=user.id, created_at=now,
    ))
    try:
        audit.log_event(
            db, user_id=user.id, action=audit_action, module="Case Management",
            detail=f"{title} (case {case.case_id})",
            target_type="suspect", target_id=audit_target_id, request=request,
        )
    except Exception:
        pass

def list_suspects(
    db: Session,
    *,
    user: User,
    case_id: str,
    request: Optional[Request],
    search: str = "",
    status_filter: str = "all",
    date_filter: str = "",     # YYYY-MM-DD — date the suspect was added
    page: int = 1,
    page_size: int = 5,
) -> CaseSuspectsList:
    case = _resolve_case(db, user=user, case_id=case_id)

    q = (
        db.query(CaseSuspect)
        .filter(CaseSuspect.case_id_fk == case.id)
        .options(
            joinedload(CaseSuspect.person),
            joinedload(CaseSuspect.status),
            joinedload(CaseSuspect.case),
        )
    )

    s = (search or "").strip()
    if s:
        like = f"%{s}%"
        q = q.outerjoin(CaseSuspect.person).filter(
            or_(
                CaseSuspect.suspect_id.ilike(like),
                Person.full_name.ilike(like),
                Person.cnic.ilike(like),
                CaseSuspect.reason.ilike(like),
                CaseSuspect.alibi.ilike(like),
                CaseSuspect.relation_to_case.ilike(like),
            )
        )

    sf = (status_filter or "all").strip()
    if sf and sf.lower() != "all":
        q = q.outerjoin(SuspectStatus, CaseSuspect.status_id == SuspectStatus.id)
        like_status = f"%{sf}%"
        q = q.filter(SuspectStatus.label.ilike(like_status))

    if date_filter:
        try:
            target = datetime.strptime(date_filter, "%Y-%m-%d").date()
            # Match on the calendar date of created_at
            from sqlalchemy import func as sa_func
            q = q.filter(sa_func.date(CaseSuspect.created_at) == target)
        except ValueError:
            pass

    total = q.distinct().count()

    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    rows = (
        q.order_by(desc(CaseSuspect.created_at), desc(CaseSuspect.id))
         .distinct()
         .limit(page_size)
         .offset((page - 1) * page_size)
         .all()
    )
    items = [_row_from_suspect(r) for r in rows]

    status_options = [
        r.label for r in
        db.query(SuspectStatus)
          .filter(SuspectStatus.active == True)  # noqa: E712
          .order_by(SuspectStatus.sort_order, SuspectStatus.label)
          .all()
    ]

    try:
        audit.log_event(
            db, user_id=user.id, action="VIEW", module="Case Management",
            detail=(
                f"Viewed suspects list (case={case_id}, search='{s}', "
                f"status='{sf}', date={date_filter}, page={page}). "
                f"Returned {len(items)}/{total}."
            ),
            target_type="suspect_list", target_id=case_id, request=request,
        )
        db.commit()
    except Exception:
        db.rollback()

    return CaseSuspectsList(
        items=items, total=total, page=page, page_size=page_size,
        status_options=status_options,
    )


def get_suspect(
    db: Session,
    *,
    user: User,
    case_id: str,
    suspect_id: str,
    request: Optional[Request],
) -> SuspectRow:
    case = _resolve_case(db, user=user, case_id=case_id)
    row = _resolve_suspect(db, case=case, suspect_id=suspect_id)

    try:
        audit.log_event(
            db, user_id=user.id, action="VIEW", module="Case Management",
            detail=f"Viewed suspect '{suspect_id}' for case {case_id}.",
            target_type="suspect", target_id=suspect_id, request=request,
        )
        db.commit()
    except Exception:
        db.rollback()

    return _row_from_suspect(row)


def update_suspect(
    db: Session,
    *,
    user: User,
    case_id: str,
    suspect_id: str,
    body: UpdateSuspectRequest,
    request: Optional[Request],
) -> SuspectRow:
    case = _resolve_case(db, user=user, case_id=case_id)
    suspect = _resolve_suspect(db, case=case, suspect_id=suspect_id)
    person = suspect.person

    changes: List[str] = []

    # Note: CNIC is a natural key. If the user changes it to a CNIC that
    # already belongs to another Person, re-link this CaseSuspect to that
    # row instead of mutating ours (which would clobber unrelated data).
    if body.cnic is not None and body.cnic.strip() and person:
        new_cnic = body.cnic.strip()
        if new_cnic != (person.cnic or ""):
            existing = (
                db.query(Person)
                .filter(Person.cnic == new_cnic, Person.id != person.id)
                .first()
            )
            if existing:
                suspect.person_id = existing.id
                person = existing
                changes.append(f"linked to existing Person (cnic={new_cnic})")
            else:
                person.cnic = new_cnic
                changes.append(f"cnic → {new_cnic}")

    person_field_map = [
        ("name",       "full_name"),
        ("age",        "age"),
        ("gender",     "gender"),
        ("contact",    "contact"),
        ("address",    "address"),
        ("occupation", "occupation"),
    ]
    if person:
        for body_attr, person_attr in person_field_map:
            new_val = getattr(body, body_attr)
            if new_val is None:
                continue
            current = getattr(person, person_attr)
            if new_val == "" and (current is None or current == ""):
                continue
            if new_val == "":
                continue
            if new_val != current:
                setattr(person, person_attr, new_val)
                changes.append(f"{body_attr} updated")

    # ── Suspect fields ─────────────────────────────────────────────────
    if body.status is not None and body.status:
        new_status_id = _suspect_status_id(db, body.status)
        if new_status_id and new_status_id != suspect.status_id:
            suspect.status_id = new_status_id
            changes.append(f"status → {body.status}")

    if body.relationToCase is not None and body.relationToCase != (suspect.relation_to_case or ""):
        suspect.relation_to_case = body.relationToCase or None
        changes.append("relation updated")

    if body.reason is not None and body.reason != (suspect.reason or ""):
        suspect.reason = body.reason or None
        changes.append("reason updated")

    if body.alibi is not None and body.alibi != (suspect.alibi or ""):
        suspect.alibi = body.alibi or None
        changes.append("alibi updated")

    if body.arrested is not None and bool(body.arrested) != bool(suspect.arrested):
        suspect.arrested = bool(body.arrested)
        changes.append(f"arrested → {body.arrested}")

    if body.criminalRecord is not None and bool(body.criminalRecord) != bool(suspect.criminal_record):
        suspect.criminal_record = bool(body.criminalRecord)
        changes.append(f"criminalRecord → {body.criminalRecord}")

    if changes:
        try:
            _log_suspect_action(
                db, case=case, user=user, request=request,
                title=f"Suspect Updated: {suspect.suspect_id}",
                description=", ".join(changes),
                audit_action="UPDATE",
                audit_target_id=suspect.suspect_id,
            )
            db.commit()
        except HTTPException:
            db.rollback(); raise
        except Exception as ex:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Update failed: {ex}")

    fresh = _resolve_suspect(db, case=case, suspect_id=suspect.suspect_id)
    return _row_from_suspect(fresh)