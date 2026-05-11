import os
import secrets
from datetime import UTC, datetime, date
from typing import List, Optional

from sqlalchemy.orm import Session, joinedload
from fastapi import HTTPException, Request

from models import (
    PersonPhoto,
    User,
    Case, Person,
    CaseVictim,
    VictimStatus,
    VictimForensicFinding, VictimTimelineEntry, VictimLegalMilestone,
    Activity,
    Severity,
    TimelineEvent, TimelineEventType,
)
from schemas.user_schema import PersonPhotoDeleteResult, PersonPhotoUploadResult
from services import audit_service as audit
from schemas.case_victim_schema import (
    VictimSummaryRow, VictimDetail,
    VictimPersonal, VictimIncident, VictimProtection,
    VictimTimelineItem, VictimLegalItem,
    CaseVictimsList,
    UpdateVictimRequest,
)
from services.service_helper import UPLOADS_ROOT, _decode_data_url, _ext_for_mime, _format_officer_name, _parse_ymd, _public_url, _resolve_case, _ymd

def _victim_status_id(db: Session, label: Optional[str]) -> Optional[int]:
    if not label:
        return None
    row = db.query(VictimStatus).filter(VictimStatus.label == label).first()
    return row.id if row else None


def _severity_id_by_label(db: Session, label: Optional[str]) -> Optional[int]:
    if not label:
        return None
    row = db.query(Severity).filter(Severity.label == label).first()
    return row.id if row else None


def _timeline_event_type_id(db: Session, code: str) -> Optional[int]:
    row = db.query(TimelineEventType).filter(TimelineEventType.code == code).first()
    return row.id if row else None


def _classify_status_variant(label: Optional[str]) -> str:
    """Map a VictimStatus.label → the variant key the JSX uses on the
    top-of-page card badge. Loose matching so unseen labels still get
    a sensible bucket."""
    s = (label or "").lower()
    if "decease" in s or "fatal" in s or "dead" in s or "killed" in s:
        return "fatal"
    if "injured" in s or "hospital" in s or "wounded" in s or "critical" in s:
        return "injured"
    if "no injury" in s or s in ("alive", "safe", "uninjured"):
        return "noInjury"
    if "missing" in s:
        return "missing"
    return "noInjury"


def _summary_from_victim(v: CaseVictim, idx: int) -> VictimSummaryRow:
    """Top-of-page card row."""
    label = v.status.label if v.status else "—"
    role = v.person.occupation if v.person else None
    return VictimSummaryRow(
        id=v.victim_id,
        title=f"Victim #{idx}",
        role=role,
        status=label,
        statusVariant=_classify_status_variant(label),
    )

def _log_victim_action(
    db: Session, *,
    case: Case, user: User, request: Optional[Request],
    title: str, description: str,
    audit_action: str = "UPDATE",
    audit_target_id: Optional[str] = None,
):
    now = datetime.now(UTC)

    db.add(TimelineEvent(
        case_id_fk=case.id,
        event_id=f"EVT-{int(now.timestamp() * 1000):X}-{secrets.token_hex(2).upper()}",
        event_source="SYSTEM",
        event_type_id=_timeline_event_type_id(db, "VICTIM_ADDED"),
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
            target_type="victim", target_id=audit_target_id or "",
            request=request,
        )
    except Exception:
        pass

def list_victims(
    db: Session,
    *,
    user: User,
    case_id: str,
    request: Optional[Request],
) -> CaseVictimsList:
    """All victims on the case — used to render the top-card tabs."""
    case = _resolve_case(db, user=user, case_id=case_id)

    rows = q = (
        db.query(CaseVictim)
        .filter(CaseVictim.case_id_fk == case.id)
        .options(
            joinedload(CaseVictim.person),
            joinedload(CaseVictim.status),
            joinedload(CaseVictim.case).joinedload(Case.case_type),
        )
    )

    items = [_summary_from_victim(r, i + 1) for i, r in enumerate(rows)]

    status_options = [
        r.label for r in
        db.query(VictimStatus)
          .filter(VictimStatus.active == True)  # noqa: E712
          .order_by(VictimStatus.sort_order, VictimStatus.label)
          .all()
    ]

    try:
        audit.log_event(
            db, user_id=user.id, action="VIEW", module="Case Management",
            detail=f"Viewed victims list (case={case_id}). Returned {len(items)}.",
            target_type="victim_list", target_id=case_id, request=request,
        )
        db.commit()
    except Exception:
        db.rollback()

    return CaseVictimsList(
        items=items, total=len(items), status_options=status_options,
    )


def get_victim(
    db: Session,
    *,
    user: User,
    case_id: str,
    victim_id: str,
    request: Optional[Request],
) -> VictimDetail:
    case = _resolve_case(db, user=user, case_id=case_id)
    v = _resolve_victim(db, case=case, victim_id=victim_id)

    try:
        audit.log_event(
            db, user_id=user.id, action="VIEW", module="Case Management",
            detail=f"Viewed victim '{victim_id}' for case {case_id}.",
            target_type="victim", target_id=victim_id, request=request,
        )
        db.commit()
    except Exception:
        db.rollback()

    return _detail_from_victim(v)


def update_victim(
    db: Session,
    *,
    user: User,
    case_id: str,
    victim_id: str,
    body: UpdateVictimRequest,
    request: Optional[Request],
) -> VictimDetail:
    case = _resolve_case(db, user=user, case_id=case_id)
    victim = _resolve_victim(db, case=case, victim_id=victim_id)
    person = victim.person

    changes: List[str] = []
    
    if body.cnic is not None and body.cnic.strip() and person:
        new_cnic = body.cnic.strip()
        if new_cnic != (person.cnic or ""):
            existing = (
                db.query(Person)
                .filter(Person.cnic == new_cnic, Person.id != person.id)
                .first()
            )
            if existing:
                victim.person_id = existing.id
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

    if body.status is not None and body.status.strip():
        new_status_id = _victim_status_id(db, body.status.strip())
        if new_status_id and new_status_id != victim.status_id:
            victim.status_id = new_status_id
            changes.append(f"status → {body.status}")

    if body.threatLevel is not None and body.threatLevel.strip():
        new_id = _severity_id_by_label(db, body.threatLevel.strip())
        if new_id and new_id != victim.threat_level_id:
            victim.threat_level_id = new_id
            changes.append(f"threatLevel → {body.threatLevel}")

    text_field_map = [
        ("primaryLabel",       "primary_label"),
        ("relation",           "relation_to_suspect"),
        ("injuryType",         "injury_type"),
        ("natureOfInjuries",   "nature_of_injuries"),
        ("causeOfDeath",       "cause_of_death"),
        ("declaredDead",       "declared_dead"),
        ("postmortemAutopsy",  "postmortem_autopsy"),
        ("injurySummary",      "injury_summary"),
        ("injuryRecordedBy",   "injury_recorded_by"),
        ("statement",          "statement"),
        ("protectionAssigned", "protection_assigned"),
        ("protectionNotes",    "protection_notes"),
    ]
    for body_attr, col in text_field_map:
        new_val = getattr(body, body_attr)
        if new_val is None:
            continue
        current = getattr(victim, col)
        if new_val == "" and (current is None or current == ""):
            continue
        normalized = new_val if new_val != "" else None
        if normalized != current:
            setattr(victim, col, normalized)
            changes.append(f"{body_attr} updated")

    bool_field_map = [
        ("medicalReport",      "medical_report"),
        ("postmortem",         "postmortem"),
        ("protectionRequired", "protection_required"),
        ("cooperative",        "cooperative"),
    ]
    for body_attr, col in bool_field_map:
        new_val = getattr(body, body_attr)
        if new_val is None:
            continue
        if bool(new_val) != bool(getattr(victim, col)):
            setattr(victim, col, bool(new_val))
            changes.append(f"{body_attr} → {bool(new_val)}")

    if body.nextFollowUp is not None:
        new_date = _parse_ymd(body.nextFollowUp) if body.nextFollowUp.strip() else None
        if new_date != victim.next_follow_up:
            victim.next_follow_up = new_date
            changes.append(
                f"nextFollowUp → {body.nextFollowUp.strip() or '—'}"
            )

    if body.forensic is not None:
        new_list = [s.strip() for s in body.forensic if s and s.strip()]
        old_list = sorted(
            v.finding_text for v in (victim.forensic_findings or []) if v.finding_text
        )
        if sorted(new_list) != old_list:
            for row in list(victim.forensic_findings or []):
                db.delete(row)
            now = datetime.now(UTC)
            for txt in new_list:
                db.add(VictimForensicFinding(
                    case_victim_id=victim.id,
                    finding_text=txt,
                    recorded_at=now,
                ))
            changes.append(f"forensic findings ({len(new_list)} item{'s' if len(new_list) != 1 else ''})")

    if body.timeline is not None:
        new_items = [
            (_parse_ymd(t.date) or victim.created_at.date() if victim.created_at else date.today(), (t.text or "").strip())
            for t in body.timeline
            if (t.text or "").strip()
        ]
        old_items = [
            (t.entry_date, (t.entry_text or "").strip())
            for t in (victim.timeline_entries or [])
        ]
        if sorted(map(str, new_items)) != sorted(map(str, old_items)):
            for row in list(victim.timeline_entries or []):
                db.delete(row)
            for d, txt in new_items:
                db.add(VictimTimelineEntry(
                    case_victim_id=victim.id,
                    entry_date=d,
                    entry_text=txt,
                ))
            changes.append(f"timeline entries ({len(new_items)} item{'s' if len(new_items) != 1 else ''})")

    if body.legal is not None:
        new_items = [
            (l.label.strip(), bool(l.done))
            for l in body.legal
            if l and l.label and l.label.strip()
        ]
        old_items = [
            ((l.label or "").strip(), bool(l.done))
            for l in (victim.legal_milestones or [])
        ]
        if sorted(map(str, new_items)) != sorted(map(str, old_items)):
            for row in list(victim.legal_milestones or []):
                db.delete(row)
            now = datetime.now(UTC)
            for label_text, done in new_items:
                db.add(VictimLegalMilestone(
                    case_victim_id=victim.id,
                    label=label_text,
                    done=done,
                    completed_at=now if done else None,
                ))
            changes.append(f"legal milestones ({len(new_items)} item{'s' if len(new_items) != 1 else ''})")

    if changes:
        try:
            display = (person.full_name if person and person.full_name else victim.victim_id)
            _log_victim_action(
                db, case=case, user=user, request=request,
                title=f"Victim Updated: {display}",
                description=", ".join(changes)[:500],
                audit_action="UPDATE",
                audit_target_id=victim.victim_id,
            )
            db.commit()
        except HTTPException:
            db.rollback()
            raise
        except Exception as ex:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Update failed: {ex}")

    fresh = _resolve_victim(db, case=case, victim_id=victim.victim_id)
    return _detail_from_victim(fresh)

def _save_victim_photo_to_disk(
    raw: bytes, mime: str, *,
    case_id: str, victim_id: str, original_name: Optional[str],
) -> str:
    """Mirror of _save_file_to_disk from case_evidence_service, but pinned
    under uploads/victims/<case>/<victim>/."""
    folder = os.path.join(UPLOADS_ROOT, "victims", case_id, victim_id)
    os.makedirs(folder, exist_ok=True)
    ext = _ext_for_mime(mime, original_name)
    fname = f"{secrets.token_hex(8)}{ext}"
    abs_path = os.path.join(folder, fname)
    with open(abs_path, "wb") as f:
        f.write(raw)
    return abs_path


def _detail_from_victim(v: CaseVictim) -> VictimDetail:
    p = v.person

    forensic = sorted(
        v.forensic_findings or [],
        key=lambda f: f.recorded_at or datetime.min,
    )
    timeline = sorted(
        v.timeline_entries or [],
        key=lambda t: t.entry_date or date.min,
    )
    legal = list(v.legal_milestones or [])
    
    photo = v.person.photo if v.person else None

    return VictimDetail(
        id=v.victim_id,
        caseId=v.case.case_id if v.case else "",
        personal=VictimPersonal(
            name=p.full_name if p else None,
            age=p.age if p else None,
            contact=p.contact if p else None,
            gender=p.gender if p else None,
            occupation=p.occupation if p else None,
            cnic=p.cnic if p else None,
        ),
        incident=VictimIncident(
            residentialAddress=p.address if p else None,
            natureOfInjuries=v.nature_of_injuries,
            causeOfDeath=v.cause_of_death,
            declaredDead=v.declared_dead,
            postmortemAutopsy=v.postmortem_autopsy,
        ),
        injurySummary=v.injury_summary,
        injuryRecordedBy=v.injury_recorded_by,
        forensic=[f.finding_text for f in forensic if f.finding_text],
        timeline=[
            VictimTimelineItem(date=_ymd(t.entry_date), text=t.entry_text)
            for t in timeline
        ],
        protection=VictimProtection(
            threatLevel=(v.threat_level.label if v.threat_level else "—"),
            protectionAssigned=(v.protection_assigned or "No"),
            notes=v.protection_notes,
        ),
        legal=[VictimLegalItem(label=l.label, done=bool(l.done)) for l in legal],
        nextFollowUp=_ymd(v.next_follow_up) or None,
        caseType=(v.case.case_type.label if (v.case and v.case.case_type) else None),
        primaryLabel=v.primary_label,
        cooperative=bool(v.cooperative),
        status=v.status.label if v.status else None,
        relation=v.relation_to_suspect,
        injuryType=v.injury_type,
        medicalReport=bool(v.medical_report),
        postmortem=bool(v.postmortem),
        protectionRequired=bool(v.protection_required),
        statement=v.statement,

        photoUrl = _public_url(photo.file_path) if photo else None
    )


def _resolve_victim(db: Session, *, case: Case, victim_id: str) -> CaseVictim:
    v = (
        db.query(CaseVictim)
        .filter(
            CaseVictim.case_id_fk == case.id,
            CaseVictim.victim_id == victim_id,
        )
        .options(
            joinedload(CaseVictim.person),
            joinedload(CaseVictim.status),
            joinedload(CaseVictim.threat_level),
            joinedload(CaseVictim.forensic_findings),
            joinedload(CaseVictim.timeline_entries),
            joinedload(CaseVictim.legal_milestones),
            joinedload(CaseVictim.case),
        )
        .first()
    )

    if not v:
        raise HTTPException(status_code=404, detail=f"Victim '{victim_id}' not found")
    return v

def add_victim_photo(db, *, user, case_id, victim_id, body, request: Optional[Request]):
    case   = _resolve_case(db, user=user, case_id=case_id)
    victim = _resolve_victim(db, case=case, victim_id=victim_id)
    person = victim.person

    raw, mime = _decode_data_url(body.dataUrl, image_only=True)

    # Delete old photo file + DB row if exists
    existing = db.query(PersonPhoto).filter(PersonPhoto.person_id == person.id).first()
    if existing:
        try: os.remove(existing.file_path)
        except Exception: pass
        db.delete(existing)
        db.flush()

    abs_path = _save_victim_photo_to_disk(raw, mime, case_id=case_id,
                   victim_id=victim_id, original_name=body.fileName)

    photo = PersonPhoto(
        person_id=person.id,
        file_path=abs_path,
        file_name=body.fileName or os.path.basename(abs_path),
        file_mime=mime,
        file_size=len(raw),
    )
    db.add(photo)
    db.flush()
    db.commit()

    return PersonPhotoUploadResult(photoUrl=_public_url(abs_path))

def delete_victim_photo(db, *, user, case_id, victim_id, request):
    case   = _resolve_case(db, user=user, case_id=case_id)
    victim = _resolve_victim(db, case=case, victim_id=victim_id)

    photo = db.query(PersonPhoto).filter(PersonPhoto.person_id == victim.person_id).first()
    if not photo:
        raise HTTPException(status_code=404, detail="No photo found")

    file_path = photo.file_path
    db.delete(photo)
    db.commit()

    try: os.remove(file_path)
    except Exception: pass

    return PersonPhotoDeleteResult(deleted=True, photoUrl=None)