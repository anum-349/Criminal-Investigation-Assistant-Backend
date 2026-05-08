from datetime import datetime, date
from typing import List, Optional, Tuple
import secrets

from sqlalchemy import desc
from sqlalchemy.orm import Session, joinedload
from fastapi import Request, HTTPException

import os                                                        # NEW
from models import EvidencePhoto                                  # NEW
from services.case_evidence_service import (                      # NEW
    _decode_data_url, _ext_for_mime,                              # NEW
    UPLOADS_ROOT,                                                 # NEW
)  

from models import (
    User, Investigator, Person,
    Case, CaseStatus, CaseType, Severity,
    Activity,
    CaseSuspect, SuspectStatus,
    CaseVictim, VictimStatus,
    CaseWitness, WitnessCredibility,
    Evidence, EvidenceType,
    Lead,
    TimelineEvent, TimelineEventType,
)
from services import audit_service as audit
from schemas.case_detail_schema import (
    CaseHeader, CaseStats, CaseDetailResponse,
    TimelineEventOut, AddResult,
    SuspectInput, EvidenceInput, VictimInput, WitnessInput,
)


# ─── ID generators ─────────────────────────────────────────────────────────
# Mirrors the JS helpers in caseEventConstants.js — short, sortable, unique.

def _short_id(prefix: str) -> str:
    ts = int(datetime.utcnow().timestamp() * 1000)
    rand = secrets.token_hex(2).upper()
    return f"{prefix}-{ts:X}-{rand}"


def _next_event_id() -> str:    return _short_id("EVT")
def _next_suspect_id() -> str:  return _short_id("S")
def _next_victim_id() -> str:   return _short_id("V")
def _next_witness_id() -> str:  return _short_id("W")
def _next_evidence_id() -> str: return _short_id("E")


# ─── Lookup helpers ────────────────────────────────────────────────────────

def _severity_id_by_label(db: Session, label: Optional[str]) -> Optional[int]:
    if not label:
        return None
    row = db.query(Severity).filter(Severity.label == label).first()
    return row.id if row else None


def _suspect_status_id(db: Session, label: Optional[str]) -> Optional[int]:
    if not label:
        # Fall back to the first row in the lookup table — every CaseSuspect.status_id
        # is non-nullable, so we have to pick something.
        row = db.query(SuspectStatus).order_by(SuspectStatus.id).first()
    else:
        row = db.query(SuspectStatus).filter(SuspectStatus.label == label).first()
        if not row:
            row = db.query(SuspectStatus).order_by(SuspectStatus.id).first()
    return row.id if row else None


def _victim_status_id(db: Session, label: Optional[str]) -> Optional[int]:
    if not label:
        row = db.query(VictimStatus).order_by(VictimStatus.id).first()
    else:
        row = db.query(VictimStatus).filter(VictimStatus.label == label).first()
        if not row:
            row = db.query(VictimStatus).order_by(VictimStatus.id).first()
    return row.id if row else None


def _credibility_id(db: Session, label: Optional[str]) -> Optional[int]:
    if not label:
        return None
    row = db.query(WitnessCredibility).filter(WitnessCredibility.label == label).first()
    return row.id if row else None


def _evidence_type_id(db: Session, label: Optional[str]) -> int:
    """Evidence.type_id is non-nullable, so we MUST resolve to something."""
    if label:
        row = db.query(EvidenceType).filter(EvidenceType.label == label).first()
        if row:
            return row.id
    # fall back to the first active type
    row = (
        db.query(EvidenceType)
        .filter(EvidenceType.active == True)  # noqa: E712
        .order_by(EvidenceType.sort_order, EvidenceType.id)
        .first()
    )
    if not row:
        raise HTTPException(
            status_code=500,
            detail="No evidence types configured. Seed lkp_evidence_types first.",
        )
    return row.id


def _timeline_event_type_id(db: Session, code: str) -> Optional[int]:
    """Look up a system-event type by its code (e.g. 'SUSPECT_ADDED')."""
    row = db.query(TimelineEventType).filter(TimelineEventType.code == code).first()
    return row.id if row else None


def _resolve_case(db: Session, *, user: User, case_id: str) -> Case:
    """Get the case and enforce ownership. Raises 404 / 403."""
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


def _resolve_person(
    db: Session,
    *,
    name: Optional[str],
    cnic: Optional[str],
    age: Optional[int] = None,
    gender: Optional[str] = None,
    contact: Optional[str] = None,
    address: Optional[str] = None,
) -> Person:
    """
    Find an existing Person by CNIC, otherwise create one. CNIC is the
    natural key (it's unique on the table), so we never duplicate someone
    just because they're added to a second case.
    """
    if cnic:
        existing = db.query(Person).filter(Person.cnic == cnic).first()
        if existing:
            # Update fields that the new entry has and the old row doesn't.
            if name and not existing.full_name:    existing.full_name = name
            if age and not existing.age:           existing.age = age
            if gender and not existing.gender:     existing.gender = gender
            if contact and not existing.contact:   existing.contact = contact
            if address and not existing.address:   existing.address = address
            return existing

    person = Person(
        full_name=name or None,
        cnic=cnic or None,
        age=age,
        gender=gender,
        contact=contact,
        address=address,
        is_unknown=not bool(name),
    )
    db.add(person)
    db.flush()      # we need person.id immediately
    return person


def _format_officer_name(user: User) -> str:
    """Used by every TimelineEvent we create."""
    rank = ""
    if user.investigator and user.investigator.rank:
        rank = f"{user.investigator.rank}. "
    return f"{rank}{user.username}"


def _ymd(d) -> str:
    if isinstance(d, datetime):
        return d.strftime("%Y-%m-%d")
    if isinstance(d, date):
        return d.strftime("%Y-%m-%d")
    return ""



# ═══ Public — Add Suspect ══════════════════════════════════════════════════

def add_suspect(
    db: Session,
    *,
    user: User,
    case_id: str,
    request: Optional[Request],
    suspects: List[SuspectInput],
) -> AddResult:
    case = _resolve_case(db, user=user, case_id=case_id)

    created_ids: List[str] = []
    timeline_out: List[TimelineEventOut] = []

    try:
        for s in suspects:
            person = _resolve_person(db, name=s.name, cnic=s.cnic, age=s.age, gender=s.gender)

            row = CaseSuspect(
                case_id_fk=case.id,
                person_id=person.id,
                suspect_id=s.suspectId or _next_suspect_id(),
                status_id=_suspect_status_id(db, s.status),
                relation_to_case=s.relationToCase,
                reason=s.reason,
                alibi=s.alibi,
                criminal_record=bool(s.criminalRecord),
                arrested=bool(s.arrested),
            )
            db.add(row)
            db.flush()
            created_ids.append(row.suspect_id)

            display = s.name or "Unnamed suspect"
            ev = _log_action(
                db, case=case, user=user, request=request,
                system_event_code="SUSPECT_ADDED",
                title=f"Suspect Added: {display}",
                description=(s.reason or "New suspect record created."),
                audit_target_type="suspect",
                audit_target_id=row.suspect_id,
            )
            timeline_out.append(_timeline_to_out(ev, case.case_id))

        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to add suspect: {e}")

    return AddResult(created_ids=created_ids, timeline_events=timeline_out)


# ═══ Public — Add Evidence ═════════════════════════════════════════════════

def add_evidence(
    db: Session,
    *,
    user: User,
    case_id: str,
    request: Optional[Request],
    evidences: List[EvidenceInput],
) -> AddResult:
    """
    Insert one row per item in `evidences`, plus any photos the user attached
    in the dialog. Each Evidence row writes the standard triple
    (TimelineEvent + Activity + AuditLog). Photo files are written to disk
    BEFORE commit so a DB rollback can also reach in and delete them.
    """
    case = _resolve_case(db, user=user, case_id=case_id)

    created_ids: List[str] = []
    timeline_out: List[TimelineEventOut] = []
    written_files: List[str] = []      # paths to clean up on rollback

    try:
        for e in evidences:
            type_id = _evidence_type_id(db, e.type)

            collected_at = None
            if e.dateCollected:
                try:
                    collected_at = datetime.strptime(e.dateCollected, "%Y-%m-%d").date()
                except ValueError:
                    collected_at = None

            row = Evidence(
                case_id_fk=case.id,
                evidence_id=e.evidenceId or _next_evidence_id(),
                type_id=type_id,
                description=e.description,
                file_name=e.fileName,
                file_mime=e.fileMime,
                date_collected=collected_at,
                collected_by=e.collectedBy or _format_officer_name(user),
            )
            db.add(row)
            db.flush()
            created_ids.append(row.evidence_id)

            # ── Photos for this row ─────────────────────────────────────
            for ph in (e.photos or []):
                if not ph.dataUrl:
                    continue
                raw, mime = _decode_data_url(ph.dataUrl)

                folder = os.path.join(UPLOADS_ROOT, "evidence",
                                       case.case_id, row.evidence_id)
                os.makedirs(folder, exist_ok=True)
                ext = _ext_for_mime(mime, ph.name)
                fname = f"{secrets.token_hex(8)}{ext}"
                abs_path = os.path.join(folder, fname)
                with open(abs_path, "wb") as f:
                    f.write(raw)
                written_files.append(abs_path)

                db.add(EvidencePhoto(
                    evidence_id=row.id,
                    file_path=abs_path,
                    file_name=ph.name or fname,
                    file_mime=mime,
                    file_size=len(raw),
                ))

            # ── Triple-write for this evidence row ──────────────────────
            label = e.type or "Evidence"
            photo_note = ""
            if e.photos:
                n = len(e.photos)
                photo_note = f" ({n} photo{'s' if n != 1 else ''} attached)"
            ev = _log_action(
                db, case=case, user=user, request=request,
                system_event_code="EVIDENCE_ADDED",
                title=f"Evidence Added: {label}",
                description=(
                    (e.description or f"Evidence {row.evidence_id} attached to case.")
                    + photo_note
                ),
                audit_target_type="evidence",
                audit_target_id=row.evidence_id,
            )
            timeline_out.append(_timeline_to_out(ev, case.case_id))

        db.commit()
    except HTTPException:
        db.rollback()
        # delete any files we wrote before the failure
        for p in written_files:
            try: os.remove(p)
            except Exception: pass
        raise
    except Exception as ex:
        db.rollback()
        for p in written_files:
            try: os.remove(p)
            except Exception: pass
        raise HTTPException(status_code=500, detail=f"Failed to add evidence: {ex}")

    return AddResult(created_ids=created_ids, timeline_events=timeline_out)

# ═══ Public — Add Victim ═══════════════════════════════════════════════════

def add_victim(
    db: Session,
    *,
    user: User,
    case_id: str,
    request: Optional[Request],
    victims: List[VictimInput],
) -> AddResult:
    case = _resolve_case(db, user=user, case_id=case_id)

    created_ids: List[str] = []
    timeline_out: List[TimelineEventOut] = []

    try:
        for v in victims:
            person = _resolve_person(
                db, name=v.name, cnic=v.cnic, age=v.age,
                gender=v.gender, contact=v.contact, address=v.address,
            )
            if v.occupation and not person.occupation:
                person.occupation = v.occupation

            row = CaseVictim(
                case_id_fk=case.id,
                person_id=person.id,
                victim_id=v.victimId or _next_victim_id(),
                status_id=_victim_status_id(db, v.status),
                primary_label=v.primaryLabel,
                injury_type=v.injuryType,
                nature_of_injuries=v.natureOfInjuries,
                cause_of_death=v.causeOfDeath,
                statement=v.statement,
            )
            db.add(row)
            db.flush()
            created_ids.append(row.victim_id)

            display = v.name or "Unnamed victim"
            sev = "Critical" if (v.status or "").lower() == "deceased" else "Normal"
            descr_parts = []
            if v.injuryType:    descr_parts.append(f"Injury: {v.injuryType}")
            if v.status:        descr_parts.append(f"Status: {v.status}")
            description = " · ".join(descr_parts) or "New victim record created."

            ev = _log_action(
                db, case=case, user=user, request=request,
                system_event_code="VICTIM_ADDED",
                title=f"Victim Added: {display}",
                description=description,
                severity_label=sev,
                audit_target_type="victim",
                audit_target_id=row.victim_id,
            )
            timeline_out.append(_timeline_to_out(ev, case.case_id))

        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to add victim: {e}")

    return AddResult(created_ids=created_ids, timeline_events=timeline_out)


# ═══ Public — Add Witness ══════════════════════════════════════════════════

def add_witness(
    db: Session,
    *,
    user: User,
    case_id: str,
    request: Optional[Request],
    witnesses: List[WitnessInput],
) -> AddResult:
    case = _resolve_case(db, user=user, case_id=case_id)

    created_ids: List[str] = []
    timeline_out: List[TimelineEventOut] = []

    try:
        for w in witnesses:
            person = _resolve_person(
                db,
                name=None if w.anonymous else w.name,
                cnic=None if w.anonymous else w.cnic,
                age=w.age, gender=w.gender,
                contact=None if w.anonymous else w.contact,
                address=None if w.anonymous else w.address,
            )

            row = CaseWitness(
                case_id_fk=case.id,
                person_id=person.id,
                witness_id=w.witnessId or _next_witness_id(),
                credibility_id=_credibility_id(db, w.credibility),
                relation_to_case=w.relationToCase,
                description=w.description,
                anonymous=bool(w.anonymous),
                protection_required=bool(w.protection_required),
                statement_recorded_by=_format_officer_name(user),
            )
            db.add(row)
            db.flush()
            created_ids.append(row.witness_id)

            display = "Anonymous witness" if w.anonymous else (w.name or "Unnamed witness")
            stmt = w.description or ""
            description = (stmt[:120] + "…") if len(stmt) > 120 else (stmt or "New witness statement attached to case.")

            ev = _log_action(
                db, case=case, user=user, request=request,
                system_event_code="WITNESS_ADDED",
                title=f"Witness Statement Recorded: {display}",
                description=description,
                audit_target_type="witness",
                audit_target_id=row.witness_id,
            )
            timeline_out.append(_timeline_to_out(ev, case.case_id))

        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to add witness: {e}")

    return AddResult(created_ids=created_ids, timeline_events=timeline_out)



# ─── Timeline / Activity / Audit triple-write ──────────────────────────────

def _log_action(
    db: Session,
    *,
    case: Case,
    user: User,
    request: Optional[Request],
    system_event_code: str,
    title: str,
    description: str,
    severity_label: str = "Normal",
    audit_action: str = "CREATE",
    audit_target_type: str = "case",
    audit_target_id: Optional[str] = None,
) -> TimelineEvent:
    """
    Single helper that writes a TimelineEvent + an Activity + an AuditLog
    for one user-initiated mutation on a case.

    Returns the TimelineEvent so the caller can include it in the response
    payload (so the frontend doesn't need to re-fetch the timeline).
    """
    now = datetime.utcnow()
    officer = _format_officer_name(user)

    # 1. TimelineEvent (system-emitted)
    ev = TimelineEvent(
        case_id_fk=case.id,
        event_id=_next_event_id(),
        event_source="SYSTEM",
        event_type_id=_timeline_event_type_id(db, system_event_code),
        title=title,
        description=description,
        officer_name=officer,
        severity_id=_severity_id_by_label(db, severity_label),
        event_date=now.date(),
        event_time=now.strftime("%H:%M"),
        created_at=now,
        editable=False,
    )
    db.add(ev)

    # 2. Activity row — type maps to the dashboard's left-border colour.
    activity_type = {
        "SUSPECT_ADDED":   "investigation",
        "EVIDENCE_ADDED":  "investigation",
        "VICTIM_ADDED":    "investigation",
        "WITNESS_ADDED":   "investigation",
        "AI_LEAD_GENERATED": "lead",
        "STATUS_CHANGED":  "update",
    }.get(system_event_code, "update")

    db.add(Activity(
        title=title,
        description=description,
        type=activity_type,
        case_id=case.id,
        user_id=user.id,
        created_at=now,
    ))

    # 3. AuditLog
    try:
        audit.log_event(
            db,
            user_id=user.id,
            action=audit_action,
            module="Case Management",
            detail=f"{title} (case {case.case_id})",
            target_type=audit_target_type,
            target_id=audit_target_id or case.case_id,
            request=request,
        )
    except Exception:
        # Audit failure must never break the user's mutation. The transaction
        # is still consistent — the caller's commit() / rollback() decides
        # whether the rest goes through.
        pass

    db.flush()    # need ev.id committed-in-session for the response
    return ev


def _timeline_to_out(ev: TimelineEvent, case_id: str) -> TimelineEventOut:
    """Convert a TimelineEvent ORM row to the response shape."""
    return TimelineEventOut(
        id=ev.event_id,
        case_id=case_id,
        event_source="system" if (ev.event_source or "").upper() == "SYSTEM" else "manual",
        event_type=ev.event_type.label if ev.event_type else (ev.event_source or ""),
        title=ev.title,
        description=ev.description,
        officer_name=ev.officer_name,
        severity=ev.severity.label if ev.severity else "Normal",
        location=ev.location,
        outcome=ev.outcome,
        date=_ymd(ev.event_date),
        time=ev.event_time,
        created_at=ev.created_at,
        editable=bool(ev.editable),
    )


# ═══ Public — GET case detail ══════════════════════════════════════════════

def get_case_detail(
    db: Session,
    *,
    user: User,
    case_id: str,
    request: Optional[Request],
) -> CaseDetailResponse:
    case = _resolve_case(db, user=user, case_id=case_id)

    # Eager-load everything the response needs so we don't N+1.
    case = (
        db.query(Case)
        .filter(Case.id == case.id)
        .options(
            joinedload(Case.case_type),
            joinedload(Case.case_status),
            joinedload(Case.priority),
            joinedload(Case.assigned_to).joinedload(Investigator.user),
        )
        .first()
    )

    # Investigator name
    inv_name = "—"
    if case.assigned_to and case.assigned_to.user:
        rank = (case.assigned_to.rank or "").strip()
        uname = case.assigned_to.user.username
        inv_name = f"{rank}. {uname}" if rank else uname

    header = CaseHeader(
        id=case.case_id,
        title=case.case_title,
        crime_type=case.case_type.label if case.case_type else "—",
        status=case.case_status.label if case.case_status else "—",
        severity=case.priority.label if case.priority else "—",
        investigator=inv_name,
        description=case.description,
    )

    # Stats
    evidence_count = db.query(Evidence).filter(Evidence.case_id_fk == case.id).count()
    suspect_count = db.query(CaseSuspect).filter(CaseSuspect.case_id_fk == case.id).count()
    victim_count = db.query(CaseVictim).filter(CaseVictim.case_id_fk == case.id).count()
    leads_count = db.query(Lead).filter(Lead.case_id_fk == case.id).count()
    days_open = (datetime.utcnow().date() - case.created_at.date()).days if case.created_at else 0

    stats = CaseStats(
        evidence_collected=evidence_count,
        suspects=suspect_count,
        investigation_leads=leads_count,
        victims=victim_count,
        days_open=max(0, days_open),
    )

    # Timeline (newest first, all of it — frontend's timeline component
    # paginates client-side already)
    events = (
        db.query(TimelineEvent)
        .filter(TimelineEvent.case_id_fk == case.id)
        .options(
            joinedload(TimelineEvent.event_type),
            joinedload(TimelineEvent.severity),
        )
        .order_by(desc(TimelineEvent.created_at))
        .all()
    )
    timeline = [_timeline_to_out(ev, case.case_id) for ev in events]

    # Audit the view
    try:
        audit.log_event(
            db, user_id=user.id, action="VIEW", module="Case Management",
            detail=f"Viewed case detail for '{case.case_id}'.",
            target_type="case", target_id=case.case_id, request=request,
        )
        db.commit()
    except Exception:
        db.rollback()

    return CaseDetailResponse(header=header, stats=stats, timeline=timeline)
