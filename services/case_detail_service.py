from datetime import UTC, datetime, date
import re
from typing import List, Optional
import secrets

from sqlalchemy import desc, select
from sqlalchemy.orm import Session, joinedload
from fastapi import status, Request, HTTPException

import os                                                        
from models import AuditLog, CaseDraft, CaseStatus, CaseUpdateFieldChange, CaseUpdateNote, EvidencePhoto, WitnessType                                  
import logging
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    User, Investigator, Person,
    Case, Severity, Activity,
    CaseSuspect, SuspectStatus,
    CaseVictim, VictimStatus,
    CaseWitness, WitnessCredibility,
    Evidence, EvidenceType, Lead,
    TimelineEvent, TimelineEventType,
)
from schemas.case_suspect_schema import SuspectInput
from services import audit_service as audit
from schemas.case_detail_schema import (
    AddTimelineResult, CaseHeader, CaseStats, CaseDetailResponse, DeleteDraftResponse, DraftDetailResponse, DraftListResponse, DraftSummary, SaveDraftRequest,
    TimelineEventOut, AddTimelineResult,
    EvidenceInput, UpdateCaseStatusResponse, VictimInput, WitnessInput,
)
from services.service_helper import UPLOADS_ROOT, _decode_data_url, _ext_for_mime, _resolve_person, _resolve_case, _format_officer_name, _ymd

log = logging.getLogger(__name__)

def _short_id(prefix: str, case_id: str, count: int) -> str:
    short_case_id = case_id.split("-")[-1]
    return f"{prefix}-{short_case_id}-{count + 1:02d}"

def _next_event_id(case_id: str, count: int) -> str:    return _short_id("EVN", case_id, count)
def _next_suspect_id(case_id: str, count: int) -> str:  return _short_id("SUS", case_id, count)
def _next_victim_id(case_id: str, count: int) -> str:   return _short_id("VIC", case_id, count)
def _next_witness_id(case_id: str, count: int) -> str:  return _short_id("WIT", case_id, count)
def _next_evidence_id(case_id: str, count: int) -> str: return _short_id("EVD", case_id, count)


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

def add_suspect(db: Session, *, user: User, case_id: str,
                request: Optional[Request], suspects: List[SuspectInput]):
    case = _resolve_case(db, user=user, case_id=case_id)
    created_ids, timeline_out = [], []

    try:
        for s in suspects:
            person = _resolve_person(
                db, name=s.name, cnic=s.cnic, age=s.age, gender=s.gender,
                contact=s.contact, address=s.address, occupation=s.occupation,
                physical_description= s.physicalDescription,
            )

            latest = (
                db.query(CaseSuspect)
                .filter(CaseSuspect.case_id_fk == case.id)
                .order_by(CaseSuspect.id.desc())
                .first()
            )
            last_num = 0
            if latest:
                try: last_num = int(latest.suspect_id.split("-")[-1])
                except (ValueError, IndexError): pass

            row = CaseSuspect(
                case_id_fk          = case.id,
                person_id           = person.id,
                suspect_id          = _next_suspect_id(case.case_id, count=last_num + 1),
                status_id           = _suspect_status_id(db, s.status),
                relation_to_case    = s.relationToCase,
                reason              = s.reason,
                alibi               = s.alibi,
                criminal_record     = bool(s.criminalRecord),
                arrested            = bool(s.arrested),
                known_affiliations  = s.knownAffiliations,
                arrival_method      = s.arrivalMethod,
                vehicle_description = s.vehicleDescription,
                notes               = s.notes,
            )
            db.add(row)
            db.flush()
            created_ids.append(row.suspect_id)

            display = s.name or "Unnamed suspect"
            ev = _log_action(
                db, case=case, user=user, request=request,
                system_event_code="SUSPECT_ADDED",
                title=f"Suspect Added: {display}",
                description=(s.reason or "New suspect record created.")[:120],
                audit_target_type="suspect",
                audit_target_id=row.suspect_id,
            )
            timeline_out.append(_timeline_to_out(ev, case.case_id))

        db.commit()
    except HTTPException:
        db.rollback(); raise
    except Exception:
        db.rollback()
        log.exception("add_suspect failed")
        raise HTTPException(status_code=500, detail="Failed to add suspect")

    return AddTimelineResult(created_ids=created_ids, timeline_events=timeline_out)

def add_evidence(
    db: Session,
    *,
    user: User,
    case_id: str,
    request: Optional[Request],
    evidences: List[EvidenceInput],
) -> AddTimelineResult:
    """
    Insert one row per item in `evidences`, plus any photos the user attached
    in the dialog. Each Evidence row writes the standard triple
    (TimelineEvent + Activity + AuditLog). Photo files are written to disk
    BEFORE commit so a DB rollback can also reach in and delete them.
    """
    case = _resolve_case(db, user=user, case_id=case_id)

    created_ids: List[str] = []
    timeline_out: List[TimelineEventOut] = []
    written_files: List[str] = []      

    try:
        for e in evidences:
            type_id = _evidence_type_id(db, e.type)

            collected_at = None
            if e.dateCollected:
                try:
                    collected_at = datetime.strptime(e.dateCollected, "%Y-%m-%d").date()
                except ValueError:
                    collected_at = None

            latest = (
                db.query(Evidence)
                .filter(Evidence.case_id_fk == case.id)
                .order_by(Evidence.id.desc())
                .first()
            )

            if latest:
                last_num = int(latest.evidence_id.split("-")[-1])
            else:
                last_num = 0

            next_num = last_num + 1

            row = Evidence(
                case_id_fk=case.id,
                evidence_id=_next_evidence_id(case.case_id, count=next_num),
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

    return AddTimelineResult(created_ids=created_ids, timeline_events=timeline_out)


def add_victim(
    db: Session,
    *,
    user: User,
    case_id: str,
    request: Optional[Request],
    victims: List[VictimInput],
) -> AddTimelineResult:
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

            if v.nextFollowUp:
                try:
                    parsed_follow_up =date.fromisoformat(v.nextFollowUp)
                except ValueError:
                    parsed_follow_up = None   # silently ignore malformed date
                    latest = (
                        db.query(CaseVictim)
                        .filter(CaseVictim.case_id_fk == case.id)
                        .order_by(CaseVictim.id.desc())
                        .first()
                    )

            if latest:
                last_num = int(latest.victim_id.split("-")[-1])
            else:
                last_num = 0

            next_num = last_num + 1

            row = CaseVictim(
                case_id_fk        = case.id,
                person_id         = person.id,
                victim_id         = _next_victim_id(case.case_id, count=next_num),
                status_id         = _victim_status_id(db, v.status),
                primary_label     = v.primaryLabel,
                injury_type       = v.injuryType,
                nature_of_injuries= v.natureOfInjuries,
                cause_of_death    = v.causeOfDeath,
                declared_dead     = v.declaredDead,
                postmortem_autopsy= v.postmortemAutopsy,
                statement         = v.statement,
        
                # NEW
                next_follow_up      = parsed_follow_up,
                protection_assigned = v.protectionAssigned,
                protection_notes    = v.notes,
                injury_summary      = v.injurySummary,
                injury_recorded_by  = v.injuryRecordedBy,
                relation_to_suspect = v.relation,
                medical_report      = bool(v.medicalReport)      if v.medicalReport      is not None else False,
                postmortem          = bool(v.postmortem)         if v.postmortem         is not None else False,
                protection_required = bool(v.protectionRequired) if v.protectionRequired is not None else False,
                cooperative         = bool(v.cooperative)        if v.cooperative        is not None else True,
            )
 
            # threat_level_id is a FK lookup, do it separately
            if v.threatLevel:
                from services.case_register_service import _priority_id  # reuses Severity lookup
                try:
                    row.threat_level_id = _priority_id(db, v.threatLevel)
                except Exception:
                    pass
        
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

    return AddTimelineResult(created_ids=created_ids, timeline_events=timeline_out)

# services/case_detail_service.py  — replace add_witness
def add_witness(
    db: Session,
    *,
    user: User,
    case_id: str,
    request: Optional[Request],
    witnesses: List[WitnessInput],
) -> AddTimelineResult:
    case = _resolve_case(db, user=user, case_id=case_id)

    created_ids:  List[str]             = []
    timeline_out: List[TimelineEventOut] = []

    try:
        for w in witnesses:
            person = _resolve_person(
                db,
                name    = None if w.anonymous else w.name,
                cnic    = None if w.anonymous else w.cnic,
                age     = w.age,
                gender  = w.gender,
                contact = None if w.anonymous else w.contact,
                address = None if w.anonymous else w.address,
            )

            latest = (
                db.query(CaseWitness)
                .filter(CaseWitness.case_id_fk == case.id)
                .order_by(CaseWitness.id.desc())
                .first()
            )
            last_num = 0
            if latest:
                try:
                    last_num = int(latest.witness_id.split("-")[-1])
                except (ValueError, IndexError):
                    last_num = 0

            row = CaseWitness(
                case_id_fk     = case.id,
                person_id      = person.id,
                witness_id     = _next_witness_id(case.case_id, count=last_num + 1),
                credibility_id = _credibility_id(db, w.credibility),
                witness_type_id= _witness_type_id(db, w.witnessType),
                relation_to_case = w.relationToCase,
                description    = w.description,
                anonymous      = bool(w.anonymous),
                protection_required = bool(w.protection_required),
                # Use caller-supplied recorded_by; fall back to logged-in user
                statement_recorded_by = (
                    w.recorded_by.strip()
                    if w.recorded_by and w.recorded_by.strip()
                    else _format_officer_name(user)
                ),
            )
            db.add(row)
            db.flush()
            created_ids.append(row.witness_id)

            display = "Anonymous witness" if w.anonymous else (w.name or "Unnamed witness")
            stmt    = w.description or ""
            desc    = (stmt[:120] + "…") if len(stmt) > 120 else (stmt or "New witness statement attached to case.")

            ev = _log_action(
                db, case=case, user=user, request=request,
                system_event_code = "WITNESS_ADDED",
                title             = f"Witness Statement Recorded: {display}",
                description       = desc,
                audit_target_type = "witness",
                audit_target_id   = row.witness_id,
            )
            timeline_out.append(_timeline_to_out(ev, case.case_id))

        db.commit()

    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        log.exception("add_witness failed")
        raise HTTPException(status_code=500, detail="Failed to add witness")

    return AddTimelineResult(created_ids=created_ids, timeline_events=timeline_out)

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
    now = datetime.now(UTC)
    officer = _format_officer_name(user)

    ev = TimelineEvent(
        case_id_fk=case.id,
        event_id=_next_event_id(case.case_id, count=len(case.timeline_events)),
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
        pass

    db.flush()    # need ev.id committed-in-session for the response
    return ev


def _timeline_to_out(ev: "TimelineEvent", case_id: str) -> "TimelineEventOut":
    """Convert a TimelineEvent ORM row to the response shape."""
    is_system = (ev.event_source or "").upper() == "SYSTEM"
    is_ai = (ev.event_source or "").upper() == "AI"

    if ev.event_type and ev.event_type.label:
        event_type_label = ev.event_type.label
    else:
        event_type_label = "Manual Entry" if not is_system else (ev.event_source or "")

    follow_up_str = None
    if ev.follow_up_date:
        try:
            follow_up_str = ev.follow_up_date.strftime("%Y-%m-%d")
        except Exception:
            follow_up_str = None

    return TimelineEventOut(
        id=ev.event_id,
        case_id=case_id,
        event_source="system" if is_system or is_ai else "manual",
        event_type=event_type_label,
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
        attachment_note=ev.attachment_note,
        follow_up_required=bool(ev.follow_up_required),
        follow_up_date=follow_up_str,
    )

def get_case_detail(
    db: Session,
    *,
    user: User,
    case_id: str,
    request: Optional[Request],
) -> CaseDetailResponse:
    case = _resolve_case(db, user=user, case_id=case_id)

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
    days_open = (datetime.now(UTC).date() - case.created_at.date()).days if case.created_at else 0

    stats = CaseStats(
        evidence_collected=evidence_count,
        suspects=suspect_count,
        investigation_leads=leads_count,
        victims=victim_count,
        days_open=max(0, days_open),
    )

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


def _resolve_new_status(db: Session, *, status_code: str) -> CaseStatus:
    row = (
        db.query(CaseStatus)
        .filter(CaseStatus.code == status_code, CaseStatus.active.is_(True))
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Case status code='{status_code}' does not exist or is inactive.",
        )
    return row


def _resolve_old_status(db: Session, *, status_id: Optional[int]) -> Optional[CaseStatus]:
    if status_id is None:
        return None
    return db.query(CaseStatus).filter(CaseStatus.id == status_id).first()


def _check_authorised(case: Case, user: User) -> None:
    if user.role in ("admin", "superadmin"):
        return
    if case.assigned_investigator_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not assigned to this case.",
        )


def _check_not_terminal(old_status: Optional[CaseStatus]) -> None:
    if old_status and old_status.is_terminal:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Case is already in terminal status '{old_status.label}' "
                "and cannot be changed. Contact an administrator to re-open it."
            ),
        )


def _check_not_same(case: Case, new_status_id: int, new_label: str) -> None:
    if case.case_status_id == new_status_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Case is already in status '{new_label}'.",
        )


# ── Public service function ──────────────────────────────────────────────────
def update_case_status(
    db: Session,
    *,
    user: User,
    case_id: str,
    status_code: str,          # ← was status_id: int
    note: Optional[str],
    request: Optional[Request],
) -> UpdateCaseStatusResponse:

    case = _resolve_case(db, user=user, case_id=case_id)
    _check_authorised(case, user)

    old_status = _resolve_old_status(db, status_id=case.case_status_id)
    new_status = _resolve_new_status(db, status_code=status_code)  # ← code-based lookup
    old_label  = old_status.label if old_status else "None"

    _check_not_terminal(old_status)
    _check_not_same(case, new_status.id, new_status.label)  # ← use resolved PK here

    now = datetime.now(UTC)
    case.case_status_id = new_status.id                     # ← assign resolved PK
    case.updated_at     = now
    if new_status.is_terminal:
        case.closed_at = now

    note_text   = note or f"Status changed to '{new_status.label}'."
    update_note = CaseUpdateNote(
        case_id_fk = case.id,
        user_id    = user.id,
        note       = note_text,
        created_at = now,
    )
    db.add(update_note)
    db.flush()

    db.add(CaseUpdateFieldChange(
        update_note_id = update_note.id,
        field_name     = "case_status_id",
        old_value      = f"{old_status.id if old_status else None} ({old_label})",
        new_value      = f"{new_status.id} ({new_status.label})",
    ))

    try:
        audit.log_event(
            db,
            user_id     = user.id,
            action      = "UPDATE",
            module      = "Case Management",
            detail      = f"Status updated: '{old_label}' → '{new_status.label}' on case {case.case_id}.",
            target_type = "case",
            target_id   = case.case_id,
            request     = request,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    return UpdateCaseStatusResponse(
        case_id        = case.case_id,
        old_status     = old_label,
        new_status     = new_status.label,
        update_note_id = update_note.id,
        updated_at     = now,
    )

_DRAFT_ID_RE = re.compile(r"^DR-(\d+)$")


def _next_draft_id(db: Session) -> str:
    """Return the next "DR-NNNN". First issued id is DR-0001."""
    rows = db.query(CaseDraft.draft_id).all()
    max_num = 0
    for (did,) in rows:
        m = _DRAFT_ID_RE.match(did or "")
        if m:
            n = int(m.group(1))
            if n > max_num:
                max_num = n
    return f"DR-{max_num + 1:04d}"


def _resolve_owned_draft(db: Session, *, user: User, draft_id: str) -> CaseDraft:
    """Find a draft by external id, scoped to the current user.

    Other people's drafts → 404, not 403. We don't reveal existence.
    """
    row = (
        db.query(CaseDraft)
        .filter(CaseDraft.draft_id == draft_id, CaseDraft.user_id == user.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Draft '{draft_id}' not found")
    return row


def _derive_title(body: SaveDraftRequest) -> str:
    """Best-effort title for the Drafts list. Order of preference:
       caller-provided → formData.caseTitle → formData.firNumber → fallback."""
    if body.title and body.title.strip():
        return body.title.strip()[:255]
    fd = body.formData or {}
    if isinstance(fd, dict):
        for key in ("caseTitle", "firNumber", "fir_number"):
            v = fd.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()[:255]
    return "Untitled draft"


def _compute_progress(form_data: dict) -> int:
    """Rough percent-complete for the drafts list.

    Mirrors the wizard's required-field set so the bar is meaningful:
      Step 1 (FIR upload or manual flag)           : 10%
      Step 2 (case details required fields)        : 30%
      Step 3 (location required fields)            : 20%
      Step 6 (crime description)                   : 20%
      Step 4/5/7/8 (optional rows)                 : 20% (any one filled)

    We deliberately leave Step 9 (AI Analysis) and Step 10 (Review) out —
    those are read-only and shouldn't move the bar.
    """
    if not isinstance(form_data, dict):
        return 0
    pct = 0

    if form_data.get("firFileName") or form_data.get("manualEntry"):
        pct += 10

    step2_keys = (
        "firNumber", "caseTitle", "caseType", "priority",
        "description", "incidentDate", "reportingDate",
    )
    if all(form_data.get(k) for k in step2_keys):
        pct += 30

    loc_ok = all(form_data.get(k) for k in ("province", "city", "address"))
    if loc_ok:
        pct += 20

    if form_data.get("crimeDescription"):
        pct += 20

    has_children = any(
        isinstance(form_data.get(k), list) and len(form_data[k]) > 0
        for k in ("victims", "suspects", "witnesses", "evidences")
    )
    if has_children:
        pct += 20

    return min(100, pct)


def _summary_from_row(row: CaseDraft) -> DraftSummary:
    fd = row.form_data or {}
    return DraftSummary(
        draftId=row.draft_id,
        title=row.title or "Untitled draft",
        caseType=fd.get("caseType") if isinstance(fd, dict) else None,
        firNumber=fd.get("firNumber") if isinstance(fd, dict) else None,
        updatedAt=row.updated_at or row.created_at,
        progressPercent=_compute_progress(fd if isinstance(fd, dict) else {}),
    )


def _detail_from_row(row: CaseDraft) -> DraftDetailResponse:
    return DraftDetailResponse(
        draftId=row.draft_id,
        title=row.title or "Untitled draft",
        formData=row.form_data or {},
        updatedAt=row.updated_at or row.created_at,
    )


# ──────────────────────────────────────────────────────────────────────────
# Public — save_draft
# ──────────────────────────────────────────────────────────────────────────

def save_draft(
    db: Session,
    *,
    user: User,
    body: SaveDraftRequest,
) -> DraftDetailResponse:
    """Create a new draft, or update an existing one in place.

    If `body.draftId` is supplied and belongs to the caller, that row is
    updated. Otherwise a fresh draft is created and its new id returned.
    The frontend stashes the returned id so subsequent autosaves hit the
    same row instead of multiplying drafts.
    """
    title = _derive_title(body)
    form_data = body.formData if isinstance(body.formData, dict) else {}

    # ── Update path ─────────────────────────────────────────────────────
    if body.draftId:
        existing = (
            db.query(CaseDraft)
            .filter(CaseDraft.draft_id == body.draftId, CaseDraft.user_id == user.id)
            .first()
        )
        if existing:
            existing.title = title
            existing.form_data = form_data
            # updated_at is auto-bumped via the onupdate handler in the model.
            try:
                db.commit()
            except Exception:
                db.rollback()
                log.exception("save_draft (update) failed")
                raise HTTPException(status_code=500, detail="Could not save draft")
            db.refresh(existing)
            return _detail_from_row(existing)
        # else fall through to create — frontend passed a stale id (draft
        # was deleted from another tab, etc.). Creating a new row is the
        # least surprising behaviour.

    # ── Create path ─────────────────────────────────────────────────────
    # Retry once on the unique-constraint race. Two concurrent saves from
    # the same user are very unlikely, but defensive code is cheap here.
    last_err = None
    for _attempt in range(2):
        new_id = _next_draft_id(db)
        row = CaseDraft(
            draft_id=new_id,
            user_id=user.id,
            title=title,
            form_data=form_data,
        )
        try:
            db.add(row)
            db.commit()
            db.refresh(row)
            return _detail_from_row(row)
        except Exception as e:
            db.rollback()
            last_err = e
            msg = str(e).lower()
            if "unique" not in msg and "duplicate" not in msg:
                break

    log.exception("save_draft (create) failed: %s", last_err)
    raise HTTPException(status_code=500, detail="Could not save draft")

def list_drafts(db: Session, *, user: User) -> DraftListResponse:
    """Return the caller's drafts, newest first."""
    rows = (
        db.query(CaseDraft)
        .filter(CaseDraft.user_id == user.id)
        .order_by(CaseDraft.updated_at.desc(), CaseDraft.id.desc())
        .all()
    )
    return DraftListResponse(items=[_summary_from_row(r) for r in rows])

def get_draft(db: Session, *, user: User, draft_id: str) -> DraftDetailResponse:
    row = _resolve_owned_draft(db, user=user, draft_id=draft_id)
    return _detail_from_row(row)

def delete_draft(db: Session, *, user: User, draft_id: str) -> DeleteDraftResponse:
    row = _resolve_owned_draft(db, user=user, draft_id=draft_id)
    try:
        db.delete(row)
        db.commit()
    except Exception:
        db.rollback()
        log.exception("delete_draft failed")
        raise HTTPException(status_code=500, detail="Could not delete draft")
    return DeleteDraftResponse(deleted=True, draftId=draft_id)