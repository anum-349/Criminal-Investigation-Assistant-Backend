"""
services/case_witness_service.py
─────────────────────────────────────────────────────────────────────────────
Powers the case-detail "Witnesses" tab (src/pages/investigator/case/[id]/CaseWitnesses.jsx).

Public functions:
  • list_witnesses()       — table + filter + pagination
  • get_witness()          — single row for the details dialog
  • update_witness()       — Update-dialog submit (PATCH)

Triple-write
────────────
Every PATCH writes one row each into:
  • timeline_events  (event_type='WITNESS_ADDED', event_source='SYSTEM')
  • activities       (so the dashboard recent feed picks it up)
  • audit_logs       (R3.2.1.1.5)
…all inside a single transaction. Failures roll back atomically.

Anonymity
─────────
When `anonymous` is set to True, the response masks name / cnic / contact /
address as None for display. The DB row keeps the underlying Person link
(so the same person can be matched across cases via CNIC), but the API
output hides those fields. If the user UN-anonymises a witness later, the
real fields surface again.
"""

import secrets
from datetime import datetime, date
from typing import List, Optional

from sqlalchemy import or_, desc, func as sa_func
from sqlalchemy.orm import Session, joinedload
from fastapi import HTTPException, Request

from models import (
    User,
    Case, Person,
    CaseWitness, WitnessCredibility, WitnessType,
    Activity,
    Severity,
    TimelineEvent, TimelineEventType,
)
from services import audit_service as audit
from schemas.case_witness_schema import (
    WitnessRow, CaseWitnessesList,
    UpdateWitnessRequest,
)


# ─── Constants ──────────────────────────────────────────────────────────────

STATUS_OPTIONS = ["Active", "Pending", "Closed", "Hostile", "Unavailable"]


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


def _resolve_witness(db: Session, *, case: Case, witness_id: str) -> CaseWitness:
    w = (
        db.query(CaseWitness)
        .filter(
            CaseWitness.case_id_fk == case.id,
            CaseWitness.witness_id == witness_id,
        )
        .options(
            joinedload(CaseWitness.person),
            joinedload(CaseWitness.credibility),
            joinedload(CaseWitness.witness_type),
            joinedload(CaseWitness.case),
        )
        .first()
    )
    if not w:
        raise HTTPException(status_code=404, detail=f"Witness '{witness_id}' not found")
    return w


def _format_officer_name(user: User) -> str:
    rank = ""
    if user.investigator and user.investigator.rank:
        rank = f"{user.investigator.rank}. "
    return f"{rank}{user.username}"


def _credibility_id(db: Session, label: Optional[str]) -> Optional[int]:
    if not label:
        return None
    row = db.query(WitnessCredibility).filter(WitnessCredibility.label == label).first()
    return row.id if row else None


def _witness_type_id(db: Session, label: Optional[str]) -> Optional[int]:
    if not label:
        return None
    row = db.query(WitnessType).filter(WitnessType.label == label).first()
    return row.id if row else None


def _severity_id_by_label(db: Session, label: Optional[str]) -> Optional[int]:
    if not label:
        return None
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
    if isinstance(d, date):
        return d.strftime("%Y-%m-%d")
    if hasattr(d, "strftime"):
        return d.strftime("%Y-%m-%d")
    return ""


def _parse_ymd(s: Optional[str]):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


# ─── Row builder ────────────────────────────────────────────────────────────

def _row_from_witness(w: CaseWitness) -> WitnessRow:
    """Convert a CaseWitness ORM row to the response shape, honouring
    anonymity by masking person fields when `anonymous=True`."""
    p = w.person
    is_anon = bool(w.anonymous)

    name    = None if is_anon else (p.full_name if p else None)
    cnic    = None if is_anon else (p.cnic       if p else None)
    contact = None if is_anon else (p.contact    if p else None)
    address = None if is_anon else (p.address    if p else None)

    return WitnessRow(
        id=w.witness_id,
        witnessId=w.witness_id,
        caseId=w.case.case_id if w.case else "",
        name=name,
        cnic=cnic,
        age=p.age if p else None,
        gender=p.gender if p else None,
        contact=contact,
        address=address,
        witnessType=w.witness_type.label if w.witness_type else None,
        relationToCase=w.relation_to_case,
        credibility=w.credibility.label if w.credibility else None,
        status=w.status or "Active",
        statementDate=_ymd(w.statement_date) or None,
        statementRecordedBy=w.statement_recorded_by,
        description=w.description,
        dateAdded=_ymd(w.created_at) or None,
        anonymous=is_anon,
        protectionRequired=bool(w.protection_required),
        cooperating=bool(w.cooperating),
    )


# ─── Triple-write helper ───────────────────────────────────────────────────

def _log_witness_action(
    db: Session, *,
    case: Case, user: User, request: Optional[Request],
    title: str, description: str,
    audit_action: str = "UPDATE",
    audit_target_id: Optional[str] = None,
):
    now = datetime.utcnow()

    db.add(TimelineEvent(
        case_id_fk=case.id,
        event_id=f"EVT-{int(now.timestamp() * 1000):X}-{secrets.token_hex(2).upper()}",
        event_source="SYSTEM",
        event_type_id=_timeline_event_type_id(db, "WITNESS_ADDED"),
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
            target_type="witness", target_id=audit_target_id or "",
            request=request,
        )
    except Exception:
        pass


# ─── 1. List ────────────────────────────────────────────────────────────────

def list_witnesses(
    db: Session,
    *,
    user: User,
    case_id: str,
    request: Optional[Request],
    search: str = "",
    status_filter: str = "all",
    date_filter: str = "",     # YYYY-MM-DD — date the witness was added
    page: int = 1,
    page_size: int = 5,
) -> CaseWitnessesList:
    """Server-side filter + paginate. Same field names the JSX uses."""
    case = _resolve_case(db, user=user, case_id=case_id)
    q = (
        db.query(CaseWitness)
        .outerjoin(WitnessType, CaseWitness.witness_type_id == WitnessType.id)
        .filter(CaseWitness.case_id_fk == case.id)
        .options(
            joinedload(CaseWitness.person),
            joinedload(CaseWitness.credibility),
            joinedload(CaseWitness.witness_type),
            joinedload(CaseWitness.case),
        )
    )

    # ── Free-text search ─────────────────────────────────────────────────
    # Match against witness_id, person fields, statement, relation.
    # Anonymous witnesses' Person fields are still searchable internally —
    # only the response masks them. (Investigators legitimately need to
    # find anonymous witnesses by their real name in the database.)
    s = (search or "").strip()
    if s:
        like = f"%{s}%"
        q = q.outerjoin(CaseWitness.person).filter(
            or_(
                CaseWitness.witness_id.ilike(like),
                Person.full_name.ilike(like),
                Person.cnic.ilike(like),
                CaseWitness.description.ilike(like),
                CaseWitness.relation_to_case.ilike(like),
            )
        )

    # ── Status filter ───────────────────────────────────────────────────
    sf = (status_filter or "all").strip().lower()
    if sf and sf != "all":
        q = q.filter(WitnessType.code == sf.upper())

    # ── Date filter (created_at) ────────────────────────────────────────
    if date_filter:
        target = _parse_ymd(date_filter)
        if target:
            q = q.filter(sa_func.date(CaseWitness.created_at) == target)

    total = q.distinct().count()

    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    rows = (
        q.order_by(desc(CaseWitness.created_at), desc(CaseWitness.id))
         .distinct()
         .limit(page_size)
         .offset((page - 1) * page_size)
         .all()
    )
    items = [_row_from_witness(r) for r in rows]

    # Lookup options for the dropdowns
    credibility_options = [
        r.label for r in
        db.query(WitnessCredibility)
          .filter(WitnessCredibility.active == True)  # noqa: E712
          .order_by(WitnessCredibility.sort_order, WitnessCredibility.label)
          .all()
    ]
    type_options = [
        r.label for r in
        db.query(WitnessType)
          .filter(WitnessType.active == True)  # noqa: E712
          .order_by(WitnessType.sort_order, WitnessType.label)
          .all()
    ]

    # Audit (best-effort)
    try:
        audit.log_event(
            db, user_id=user.id, action="VIEW", module="Case Management",
            detail=(
                f"Viewed witnesses list (case={case_id}, search='{s}', "
                f"status='{status_filter}', date={date_filter}, page={page}). "
                f"Returned {len(items)}/{total}."
            ),
            target_type="witness_list", target_id=case_id, request=request,
        )
        db.commit()
    except Exception:
        db.rollback()

    return CaseWitnessesList(
        items=items, total=total, page=page, page_size=page_size,
        status_options=STATUS_OPTIONS,
        credibility_options=credibility_options,
        type_options=type_options,
    )


# ─── 2. Get one (View Details dialog) ──────────────────────────────────────

def get_witness(
    db: Session,
    *,
    user: User,
    case_id: str,
    witness_id: str,
    request: Optional[Request],
) -> WitnessRow:
    case = _resolve_case(db, user=user, case_id=case_id)
    row = _resolve_witness(db, case=case, witness_id=witness_id)

    try:
        audit.log_event(
            db, user_id=user.id, action="VIEW", module="Case Management",
            detail=f"Viewed witness '{witness_id}' for case {case_id}.",
            target_type="witness", target_id=witness_id, request=request,
        )
        db.commit()
    except Exception:
        db.rollback()

    return _row_from_witness(row)


# ─── 3. Update ──────────────────────────────────────────────────────────────

def update_witness(
    db: Session,
    *,
    user: User,
    case_id: str,
    witness_id: str,
    body: UpdateWitnessRequest,
    request: Optional[Request],
) -> WitnessRow:
    case = _resolve_case(db, user=user, case_id=case_id)
    witness = _resolve_witness(db, case=case, witness_id=witness_id)
    person = witness.person

    changes: List[str] = []

    # ── Person fields ──────────────────────────────────────────────────
    # Same convention as case_suspect_service / case_victim_service:
    # empty string == "leave alone".
    if body.cnic is not None and body.cnic.strip() and person:
        new_cnic = body.cnic.strip()
        if new_cnic != (person.cnic or ""):
            existing = (
                db.query(Person)
                .filter(Person.cnic == new_cnic, Person.id != person.id)
                .first()
            )
            if existing:
                witness.person_id = existing.id
                person = existing
                changes.append(f"linked to existing Person (cnic={new_cnic})")
            else:
                person.cnic = new_cnic
                changes.append(f"cnic → {new_cnic}")

    person_field_map = [
        ("name",     "full_name"),
        ("age",      "age"),
        ("gender",   "gender"),
        ("contact",  "contact"),
        ("address",  "address"),
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

    # ── Witness Type (FK) ──────────────────────────────────────────────
    if body.witnessType is not None and body.witnessType.strip():
        new_id = _witness_type_id(db, body.witnessType.strip())
        if new_id and new_id != witness.witness_type_id:
            witness.witness_type_id = new_id
            changes.append(f"witnessType → {body.witnessType}")

    # ── Credibility (FK) ───────────────────────────────────────────────
    if body.credibility is not None and body.credibility.strip():
        new_id = _credibility_id(db, body.credibility.strip())
        if new_id and new_id != witness.credibility_id:
            witness.credibility_id = new_id
            changes.append(f"credibility → {body.credibility}")

    # ── Plain text columns ─────────────────────────────────────────────
    text_field_map = [
        ("relationToCase",      "relation_to_case"),
        ("statementRecordedBy", "statement_recorded_by"),
        ("description",         "description"),
        ("status",              "status"),
    ]
    for body_attr, col in text_field_map:
        new_val = getattr(body, body_attr)
        if new_val is None:
            continue
        current = getattr(witness, col)
        if new_val == "" and (current is None or current == ""):
            continue
        normalized = new_val if new_val != "" else None
        if normalized != current:
            setattr(witness, col, normalized)
            changes.append(f"{body_attr} updated")

    # ── Statement date ─────────────────────────────────────────────────
    if body.statementDate is not None:
        new_date = _parse_ymd(body.statementDate) if body.statementDate.strip() else None
        if new_date != witness.statement_date:
            witness.statement_date = new_date
            changes.append(f"statementDate → {body.statementDate.strip() or '—'}")

    # ── Booleans ───────────────────────────────────────────────────────
    bool_field_map = [
        ("anonymous",          "anonymous"),
        ("protectionRequired", "protection_required"),
        ("cooperating",        "cooperating"),
    ]
    for body_attr, col in bool_field_map:
        new_val = getattr(body, body_attr)
        if new_val is None:
            continue
        if bool(new_val) != bool(getattr(witness, col)):
            setattr(witness, col, bool(new_val))
            changes.append(f"{body_attr} → {bool(new_val)}")

    # ── Commit ─────────────────────────────────────────────────────────
    if changes:
        try:
            display = (
                "Anonymous witness" if witness.anonymous
                else (person.full_name if person and person.full_name else witness.witness_id)
            )
            _log_witness_action(
                db, case=case, user=user, request=request,
                title=f"Witness Updated: {display}",
                description=", ".join(changes)[:500],
                audit_action="UPDATE",
                audit_target_id=witness.witness_id,
            )
            db.commit()
        except HTTPException:
            db.rollback()
            raise
        except Exception as ex:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Update failed: {ex}")

    fresh = _resolve_witness(db, case=case, witness_id=witness.witness_id)
    return _row_from_witness(fresh)