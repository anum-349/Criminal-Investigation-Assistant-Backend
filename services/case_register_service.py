from datetime import UTC, datetime, date
from typing import List, Optional
import logging
import re

from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from fastapi import Request, HTTPException
from services.case_linker_hook import enqueue_linking

from models import (
    User, Investigator,
    Case, CaseType, CaseStatus, Severity,
    Location, Province, City,
    MurderDetails, SexualAssaultDetails, TheftDetails,
    TimelineEvent, TimelineEventType,
    Activity,
)
from schemas.case_register_schema import (
    CaseRegisterRequest,
    CaseRegisterResponse,
    FIRFileUploadRequest,
    FIRFileUploadResult,
)
from schemas.case_detail_schema import TimelineEventOut
from services import audit_service as audit
from services.service_helper import (
    _format_officer_name,
    _resolve_case,
    _decode_data_url,
    _ext_for_mime,
    UPLOADS_ROOT,
    UPLOADS_URL_PREFIX,
)

from services import notification_service as notif

import os
import secrets

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# Lookup helpers
# ──────────────────────────────────────────────────────────────────────────

def _case_type_id(db: Session, label: Optional[str]) -> int:
    """Resolve CaseType.label → id. Raises 422 if not found, because the
    Case row has a NOT-NULL FK and we can't silently default."""
    if not label:
        raise HTTPException(status_code=422, detail="caseType is required")
    row = db.query(CaseType).filter(CaseType.label == label).first()
    if not row:
        # Try a loose, case-insensitive match before giving up — case-type
        # labels in the wizard sometimes have stray whitespace or different
        # capitalisation than the seed data ("Robbery / Armed Robbery" vs
        # "robbery / armed robbery").
        row = (
            db.query(CaseType)
            .filter(func.lower(CaseType.label) == label.strip().lower())
            .first()
        )
    if not row:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown case type: '{label}'. Add it to lkp_case_types first.",
        )
    return row.id


def _case_status_id(db: Session, label: Optional[str]) -> int:
    """Resolve CaseStatus.label → id, defaulting to whatever the first
    non-terminal status is (usually 'Open')."""
    if label:
        row = db.query(CaseStatus).filter(CaseStatus.label == label).first()
        if row:
            return row.id
    # Fallback: first non-terminal status by sort order
    row = (
        db.query(CaseStatus)
        .filter(CaseStatus.is_terminal == False)  # noqa: E712
        .order_by(CaseStatus.sort_order, CaseStatus.id)
        .first()
    )
    if not row:
        # Last resort: anything
        row = db.query(CaseStatus).order_by(CaseStatus.id).first()
    if not row:
        raise HTTPException(
            status_code=500,
            detail="No case statuses configured. Seed lkp_case_statuses first.",
        )
    return row.id


def _priority_id(db: Session, label: Optional[str]) -> int:
    """Severity.label → id (Severity is reused for case priority)."""
    if not label:
        raise HTTPException(status_code=422, detail="priority is required")
    row = db.query(Severity).filter(Severity.label == label).first()
    if not row:
        row = (
            db.query(Severity)
            .filter(func.lower(Severity.label) == label.strip().lower())
            .first()
        )
    if not row:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown priority: '{label}'.",
        )
    return row.id


def _province_id(db: Session, label: Optional[str]) -> int:
    if not label:
        raise HTTPException(status_code=422, detail="location.province is required")
    row = db.query(Province).filter(Province.label == label).first()
    if not row:
        row = (
            db.query(Province)
            .filter(func.lower(Province.label) == label.strip().lower())
            .first()
        )
    if not row:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown province: '{label}'.",
        )
    return row.id


def _city_id(db: Session, *, province_id: int, name: Optional[str]) -> int:
    """City lookup is scoped to the province, since the same name can
    exist in multiple provinces. Falls back to creating the city if it
    doesn't exist — this matches how the wizard lets investigators type
    a free-form city name from the map picker."""
    if not name:
        raise HTTPException(status_code=422, detail="location.city is required")
    name = name.strip()

    row = (
        db.query(City)
        .filter(City.province_id == province_id, City.name == name)
        .first()
    )
    if row:
        return row.id

    # Case-insensitive retry
    row = (
        db.query(City)
        .filter(City.province_id == province_id, func.lower(City.name) == name.lower())
        .first()
    )
    if row:
        return row.id

    # Auto-create — the wizard supports arbitrary city names from OSM,
    # so blocking on a missing lookup row would be too strict.
    new_city = City(
        province_id=province_id,
        name=name,
        sort_order=999,
        active=True,
    )
    db.add(new_city)
    db.flush()
    return new_city.id


def _timeline_event_type_id(db: Session, code: str) -> Optional[int]:
    """CASE_REGISTERED → TimelineEventType.id. Returns None if the seed
    is missing, in which case the event row uses event_type_id=NULL — the
    JSX fall-back will still render it from event_source."""
    row = db.query(TimelineEventType).filter(TimelineEventType.code == code).first()
    return row.id if row else None


# ──────────────────────────────────────────────────────────────────────────
# case_id generation
# ──────────────────────────────────────────────────────────────────────────

# Match "C-2053", "C-2040", … — the convention used everywhere in the
# codebase (see CaseHeader docstrings).
_CASE_ID_RE = re.compile(r"^C-(\d+)$")


def _next_case_id(db: Session) -> str:
    """Return the next external case_id, e.g. 'C-2054'.

    Strategy: look at every existing case_id matching 'C-<int>', pick
    the maximum number, add 1. If none exist, start at C-2001 (matches
    the demo data in the mocked design).

    NOTE: this is *advisory* — under heavy concurrent load you'd want a
    sequence or a row lock. For the single-investigator workflow the
    project targets, this is fine. If the unique constraint on case_id
    ever fires, the caller retries.
    """
    latest = (
        db.query(Case)
        .order_by(Case.id.desc())
        .first()
    )
    last_num = 0
    if latest:
        try: last_num = int(latest.case_id.split("-")[-1])
        except (ValueError, IndexError): pass
    
    last_num = last_num + 1
    return f"CASE-2026-{last_num + 1:02d}"


# ──────────────────────────────────────────────────────────────────────────
# Crime-type subtype writer
# ──────────────────────────────────────────────────────────────────────────

def _maybe_persist_subtype(
    db: Session, *, case: Case, case_type_label: str, crime
) -> None:
    """Insert the appropriate Murder/SexualAssault/Theft row when the
    case type matches one of the subtype-bearing categories.

    Matching is intentionally loose ("contains") because case-type labels
    in the seed have variants like 'Murder / Homicide', 'Sexual Assault /
    Rape', 'Theft / Burglary' — we don't want a seed-rename to break
    this every time.
    """
    if not case_type_label:
        return
    label = case_type_label.lower()

    if "murder" in label or "homicide" in label:
        db.add(MurderDetails(
            case_id_fk=case.id,
            cause_of_death=(crime.causeOfDeath or None),
            body_location=(crime.bodyLocation or None),
            time_of_death=(crime.timeOfDeath or None),
            postmortem_done=bool(crime.postmortemDone),
            forensic_done=bool(crime.forensicDone),
        ))
        return

    if "sexual" in label or "rape" in label or "assault" in label:
        db.add(SexualAssaultDetails(
            case_id_fk=case.id,
            medical_exam=(crime.medicalExam or None),
            victim_counseling=bool(crime.victimCounseling),
            protection_order=bool(crime.protectionOrder),
            confidential_notes=None,
        ))
        return

    if "theft" in label or "burglary" in label or "robbery" in label:
        db.add(TheftDetails(
            case_id_fk=case.id,
            stolen_items=(crime.stolenItems or None),
            stolen_value=crime.stolenValue,
            recovery_status=(crime.recoveryStatus or None),
            entry_point=(crime.entryPoint or None),
        ))
        return

    # No subtype for this case type — that's fine. Common crime-detail
    # columns are stored on the Case row itself; the subtype tables are
    # only for the type-specific extras.


# ──────────────────────────────────────────────────────────────────────────
# Timeline + activity + audit (triple write)
# ──────────────────────────────────────────────────────────────────────────

def _log_registration(
    db: Session,
    *,
    case: Case,
    user: User,
    request: Optional[Request],
) -> TimelineEvent:
    """One TimelineEvent + one Activity + one AuditLog for a fresh case."""

    type_id = _timeline_event_type_id(db, "CASE_REGISTERED")
    now = datetime.now(UTC)

    ev = TimelineEvent(
        case_id_fk=case.id,
        event_id=f"EVN-{case.case_id.split('-')[-1]}-01",
        event_source="SYSTEM",
        event_type_id=type_id,
        title=f"Case Registered: {case.case_title}",
        description=(case.description or "")[:500],
        officer_name=_format_officer_name(user)[:150],
        location=None,
        outcome=None,
        event_date=case.reporting_date or date.today(),
        event_time=case.reporting_time or now.strftime("%H:%M"),
        created_at=now,
        editable=False,  # SYSTEM events aren't user-editable
    )
    db.add(ev)
    db.add(Activity(
        title=ev.title,
        description=ev.description or "",
        type="case_registered",
        case_id=case.id,
        user_id=user.id,
        created_at=now,
    ))
    db.flush()

    # Audit (best-effort — failure here doesn't block the case creation).
    try:
        audit.log_event(
            db,
            user_id=user.id,
            action="CREATE",
            module="Case Management",
            detail=f"Registered new case '{case.case_id}' (FIR {case.fir_number}).",
            target_type="case",
            target_id=case.case_id,
            request=request,
        )
    except Exception:
        log.exception("audit.log_event failed during register_case")

    try:
        notif.push(
            db,
            user_id=user.id,
            type="CASE_UPDATE",
            title=f"Case Registered: {case.case_id}",
            message=f"{case.case_title} (FIR {case.fir_number}) is now in your active caseload.",
            link_url=f"/investigator/case/{case.case_id}",
            related_case_id=case.id,
            severity_label=(case.priority.label if case.priority else "Normal"),
        )
    except Exception:
        log.exception("notification push failed during register_case")
    return ev


# ──────────────────────────────────────────────────────────────────────────
# Public: register_case
# ──────────────────────────────────────────────────────────────────────────

def register_case(
    db: Session,
    *,
    user: User,
    body: CaseRegisterRequest,
    request: Optional[Request],
) -> CaseRegisterResponse:
    """Create a Case + Location + (optional) crime-type subtype row
    in one transaction. See module docstring for the full picture.
    """

    # 1. Duplicate-FIR guard. The DB has a unique constraint on
    # cases.fir_number, but we want a nice 409 instead of a 500 stack
    # trace, so we check up-front.
    existing = (
        db.query(Case)
        .filter(Case.fir_number == body.firNumber, Case.is_deleted == False)  # noqa: E712
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=(
                f"A case with FIR number '{body.firNumber}' already exists "
                f"(case_id={existing.case_id})."
            ),
        )

    # 2. Resolve all lookup labels → ids BEFORE we start writing rows,
    # so a 422 lands early without a partial transaction to roll back.
    case_type_id   = _case_type_id  (db, body.caseType)
    case_status_id = _case_status_id(db, body.caseStatus)
    priority_id    = _priority_id   (db, body.priority)

    province_id = _province_id(db, body.location.province)
    city_id     = _city_id    (db, province_id=province_id, name=body.location.city)

    # 3. Investigator assignment. Investigators always end up assigned
    # to themselves; admins can pass `assignedInvestigator` (a username)
    # to assign someone else. If that lookup fails, we silently fall back
    # to the caller — better than a 422 here.
    assigned_inv_id = None
    if user.role == "admin" and body.assignedInvestigator:
        target = (
            db.query(User)
            .filter(User.username == body.assignedInvestigator.strip())
            .first()
        )
        if target:
            assigned_inv_id = target.id
    if assigned_inv_id is None:
        assigned_inv_id = user.id

    # 4. Generate the next case_id. We retry once on a UniqueConstraint
    # violation to handle the (very small) race window between
    # _next_case_id and the INSERT.
    new_case_id = _next_case_id(db)

    # 5. Parse dates. The schema's validator already enforced the format,
    # so fromisoformat is safe here.
    incident_d  = date.fromisoformat(body.incidentDate)
    reporting_d = date.fromisoformat(body.reportingDate)

    # 6. Build the Case row.
    case = Case(
        case_id=new_case_id,
        fir_number=body.firNumber.strip(),

        fir_file_path=None,
        fir_file_name=None,
        fir_language=body.firLanguage or "English",
        fir_text_raw=None,
        fir_text_clean=None,
        manual_entry=bool(body.manualEntry),

        case_type_id=case_type_id,
        case_status_id=case_status_id,
        priority_id=priority_id,

        case_title=body.caseTitle.strip()[:255],
        ppc_sections=(body.ppcSections or None),
        description=body.description.strip(),

        incident_date=incident_d,
        incident_time=(body.incidentTime or None),
        reporting_date=reporting_d,
        reporting_time=(body.reportingTime or None),

        reporting_officer=(body.reportingOfficer or None),
        assigned_investigator_id=assigned_inv_id,

        is_deleted=False,
    )

    # ─ Crime-detail fields that live directly on Case ─────────────────
    # (Anything generic enough that doesn't belong in a subtype table.)
    # We use setattr so this still works if some of these columns don't
    # exist on the Case model in a given migration. Models that DO have
    # the column get populated; models that don't silently skip.
    crime_common = {
        "weapon_used":        body.crime.weaponUsed,
        "weapon_description": body.crime.weaponDescription,
        "vehicle_used":       body.crime.vehicleUsed,
        "num_suspects":       body.crime.numSuspects,
        "motive":             body.crime.motive,
        "modus_operandi":     body.crime.modus,
        "witness_available":  bool(body.crime.witnessAvailable),
        "cctv_available":     bool(body.crime.cctv),
        "crime_description":  body.crime.crimeDescription,
    }
    for col, val in crime_common.items():
        if hasattr(case, col) and val is not None:
            setattr(case, col, val)

    try:
        db.add(case)
        db.flush()  # we need case.id below for Location + subtype FKs

        # 7. Location row. case.id is now populated.
        loc = Location(
            case_id_fk=case.id,
            province_id=province_id,
            city_id=city_id,
            area=(body.location.area or None),
            police_station=(body.location.policeStation or None),
            full_address=body.location.address.strip(),
            display_address=(body.location.address or None),
            latitude=body.location.latitude,
            longitude=body.location.longitude,
            crime_scene_type=(body.location.crimeSceneType or None),
            scene_access=(body.location.sceneAccess or "Secured by Police"),
            landmarks=(body.location.landmarks or None),
        )
        db.add(loc)

        # 8. Crime-type subtype.
        _maybe_persist_subtype(
            db, case=case,
            case_type_label=body.caseType,
            crime=body.crime,
        )

        ev = _log_registration(db, case=case, user=user, request=request)
        
        enqueue_linking(
        db,
        case_internal_id=case.id,
        actor_user_id=user.id,
        reason="case_registered",
        )
 
        db.commit()


    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        log.exception("register_case failed")
        # 23505 = Postgres unique violation; SQLite has a different code,
        # so we just check the message for "UNIQUE" too.
        msg = str(e).lower()
        if "unique" in msg or "duplicate" in msg:
            raise HTTPException(
                status_code=409,
                detail="A case with this FIR number or case id already exists.",
            )
        raise HTTPException(
            status_code=500,
            detail="Failed to register case. Please retry.",
        )

    # 10. Refresh + shape the response.
    db.refresh(case)
    db.refresh(ev)

    timeline_out = [
        _timeline_event_out(ev, case.case_id),
    ]

    return CaseRegisterResponse(
        case_id=case.case_id,
        fir_number=case.fir_number,
        timeline_events=timeline_out,
    )


def _timeline_event_out(ev: TimelineEvent, case_id: str) -> TimelineEventOut:
    """Shape one TimelineEvent for the response. Same convention as
    case_detail_service._timeline_to_out, duplicated here to avoid a
    circular import. If you find yourself maintaining two copies, move
    it to service_helper.py."""
    return TimelineEventOut(
        id=ev.event_id,
        case_id=case_id,
        event_source="system",
        event_type=ev.event_type.label if ev.event_type else "Case Registered",
        title=ev.title,
        description=ev.description,
        officer_name=ev.officer_name,
        severity="Normal",
        location=ev.location,
        outcome=ev.outcome,
        date=(ev.event_date.strftime("%Y-%m-%d") if ev.event_date else ""),
        time=ev.event_time,
        created_at=ev.created_at,
        editable=bool(ev.editable),
    )


# ──────────────────────────────────────────────────────────────────────────
# Public: upload_fir_file
# ──────────────────────────────────────────────────────────────────────────

def upload_fir_file(
    db: Session,
    *,
    user: User,
    case_id: str,
    body: FIRFileUploadRequest,
    request: Optional[Request],
) -> FIRFileUploadResult:
    """Decode the data URL, persist the FIR file under
    uploads/cases/{case_id}/fir/<random>.<ext>, and update the Case row.

    Same flow case_evidence_service uses for an evidence file —
    we just write to a different folder. The frontend posts this
    immediately after a successful POST /cases.
    """
    case = _resolve_case(db, user=user, case_id=case_id)

    raw, mime = _decode_data_url(body.fileDataUrl)
    if not raw:
        raise HTTPException(status_code=422, detail="Invalid fileDataUrl")

    folder = os.path.join(UPLOADS_ROOT, "cases", case_id, "fir")
    os.makedirs(folder, exist_ok=True)

    ext = _ext_for_mime(mime or body.fileMime, body.fileName)
    fname = f"{secrets.token_hex(8)}{ext}"
    abs_path = os.path.join(folder, fname)
    with open(abs_path, "wb") as f:
        f.write(raw)

    # Store the public URL — same convention as person photos / evidence.
    rel = os.path.relpath(abs_path, UPLOADS_ROOT).replace(os.sep, "/")
    public_url = f"{UPLOADS_URL_PREFIX.rstrip('/')}/{rel}"

    case.fir_file_path = public_url
    case.fir_file_name = body.fileName or fname

    try:
        audit.log_event(
            db,
            user_id=user.id,
            action="UPDATE",
            module="Case Management",
            detail=f"Uploaded FIR file for case '{case.case_id}'.",
            target_type="case",
            target_id=case.case_id,
            request=request,
        )
        db.commit()
    except Exception:
        db.rollback()
        # Roll back the FS write so we don't keep an orphan upload.
        try:
            os.remove(abs_path)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="Failed to save FIR file")

    return FIRFileUploadResult(
        fir_file_url=public_url,
        fir_file_name=case.fir_file_name,
    )