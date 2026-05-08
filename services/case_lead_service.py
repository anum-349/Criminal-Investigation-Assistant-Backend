import secrets
from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy import or_, desc
from sqlalchemy.orm import Session, joinedload
from fastapi import HTTPException, Request

from models import (
    User, Investigator,
    Case,
    Lead, LeadType, LeadStatus,
    Severity,
    CaseSuspect,
    Activity,
    TimelineEvent, TimelineEventType,
)
from services import audit_service as audit
from schemas.case_lead_schema import (
    LeadRow, LeadCounts, LeadSuspectRef,
    CaseLeadsList,
    AddManualLeadRequest, UpdateLeadStatusRequest,
    DeleteLeadResult,
)


# ─── Lookup-code groups ─────────────────────────────────────────────────────
# LEAD_STATUSES from the JS constants:
#   "New", "Under Review", "In Progress", "Actioned", "Dismissed"
# Mirror those as codes in lkp_lead_statuses (NEW, UNDER_REVIEW, IN_PROGRESS,
# ACTIONED, DISMISSED).
STATUS_LABEL_TO_CODE = {
    "New":          "NEW",
    "Under Review": "UNDER_REVIEW",
    "In Progress":  "IN_PROGRESS",
    "Actioned":     "ACTIONED",
    "Dismissed":    "DISMISSED",
}

EVENT_SOURCE_AI     = "AI"
EVENT_SOURCE_MANUAL = "MANUAL"


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


def _resolve_lead(db: Session, *, case: Case, lead_id: str) -> Lead:
    row = (
        db.query(Lead)
        .filter(Lead.case_id_fk == case.id, Lead.lead_id == lead_id)
        .options(
            joinedload(Lead.type),
            joinedload(Lead.status),
            joinedload(Lead.severity),
            joinedload(Lead.suggested_suspect),
            joinedload(Lead.similar_case),
            joinedload(Lead.created_by),
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Lead '{lead_id}' not found")
    return row


def _format_officer_name(user: User) -> str:
    rank = ""
    if user.investigator and user.investigator.rank:
        rank = f"{user.investigator.rank}. "
    return f"{rank}{user.username}"


def _next_lead_id() -> str:
    """Match the JS generateLeadId() format: LEAD-{ts}-{rand}."""
    ts = int(datetime.utcnow().timestamp() * 1000)
    return f"LEAD-{ts:X}-{secrets.token_hex(2).upper()}"


def _lead_type_id(db: Session, label: Optional[str]) -> Optional[int]:
    if not label:
        return None
    row = db.query(LeadType).filter(LeadType.label == label).first()
    return row.id if row else None


def _lead_status_id_by_label(db: Session, label: str) -> int:
    """LeadStatus.status_id is non-nullable. Resolve via code lookup, then
    label, then fall back to the first row in the table."""
    code = STATUS_LABEL_TO_CODE.get(label)
    row = None
    if code:
        row = db.query(LeadStatus).filter(LeadStatus.code == code).first()
    if not row:
        row = db.query(LeadStatus).filter(LeadStatus.label == label).first()
    if not row:
        row = db.query(LeadStatus).order_by(LeadStatus.id).first()
    if not row:
        raise HTTPException(
            status_code=500,
            detail="No lead statuses configured. Seed lkp_lead_statuses first.",
        )
    return row.id


def _severity_id_by_label(db: Session, label: Optional[str]) -> Optional[int]:
    if not label:
        return None
    row = db.query(Severity).filter(Severity.label == label).first()
    return row.id if row else None


def _timeline_event_type_id(db: Session, code: str) -> Optional[int]:
    row = db.query(TimelineEventType).filter(TimelineEventType.code == code).first()
    return row.id if row else None


def _resolve_suggested_suspect(
    db: Session, *, case: Case, name_or_id: Optional[str]
) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    """
    Try to map the form's free-text "Suggested Suspect" to a real CaseSuspect
    row (if it looks like a SUS-XXXX id and exists for this case).

    Returns: (suspect_fk_id, display_name, suspect_external_id)
    - suspect_fk_id is what gets persisted as Lead.suggested_suspect_id
    - display_name and suspect_external_id are for the response shape
    """
    if not name_or_id:
        return (None, None, None)

    # If the input looks like an external suspect id, try to match it
    candidate = name_or_id.strip()
    if candidate.upper().startswith(("SUS-", "S-", "SUSP-")):
        row = (
            db.query(CaseSuspect)
            .filter(
                CaseSuspect.suspect_id == candidate,
                CaseSuspect.case_id_fk == case.id,
            )
            .first()
        )
        if row:
            person = row.person
            display_name = person.full_name if person and person.full_name else candidate
            return (row.id, display_name, row.suspect_id)

    # Otherwise treat the whole string as a free-text name (no DB link).
    return (None, candidate, None)


def _resolve_similar_case(db: Session, *, case_id_str: Optional[str]) -> Optional[int]:
    """Map free-text 'C-2040' → cases.id PK (if it exists)."""
    if not case_id_str:
        return None
    row = (
        db.query(Case)
        .filter(Case.case_id == case_id_str.strip(), Case.is_deleted == False)  # noqa: E712
        .first()
    )
    return row.id if row else None


# ─── Description suffix encoder/decoder ─────────────────────────────────────
# Pack/unpack the optional fields that have no column in the Lead model.

_SUFFIX_MARKER = "\n\n[Lead-extras]"
_KEY_NAMES = ("Source", "Officer", "Weapon/MO", "Area", "Suspect basis")


def _encode_extras(
    *,
    source: Optional[str],
    officer: Optional[str],
    weapon: Optional[str],
    area: Optional[str],
    suspect_basis: Optional[str],
) -> str:
    parts = []
    if source:        parts.append(f"Source: {source}")
    if officer:       parts.append(f"Officer: {officer}")
    if weapon:        parts.append(f"Weapon/MO: {weapon}")
    if area:          parts.append(f"Area: {area}")
    if suspect_basis: parts.append(f"Suspect basis: {suspect_basis}")
    if not parts:
        return ""
    return f"{_SUFFIX_MARKER} {' | '.join(parts)}"


def _decode_extras(description: Optional[str]) -> Tuple[str, dict]:
    """Strip the suffix off and return (clean_description, extras_dict)."""
    if not description or _SUFFIX_MARKER not in description:
        return (description or "", {})
    clean, _, suffix = description.partition(_SUFFIX_MARKER)
    extras = {}
    suffix = suffix.strip()
    for part in suffix.split(" | "):
        if ":" in part:
            k, _, v = part.partition(":")
            extras[k.strip()] = v.strip()
    return (clean.rstrip(), extras)


# ─── Row formatter ──────────────────────────────────────────────────────────

def _row_from_lead(lead: Lead) -> LeadRow:
    clean_desc, extras = _decode_extras(lead.description)

    # Source label for the response — AI leads always say "AI Analysis",
    # manual leads use the user-picked source string from the description suffix.
    if (lead.event_source or "").upper() == EVENT_SOURCE_AI:
        source = "AI Analysis"
    else:
        source = extras.get("Source") or "Manual Entry"

    officer_name = extras.get("Officer")
    if not officer_name and lead.created_by:
        rank = ""
        if lead.created_by.investigator and lead.created_by.investigator.rank:
            rank = f"{lead.created_by.investigator.rank}. "
        officer_name = f"{rank}{lead.created_by.username}"

    suggested = None
    if lead.suggested_suspect:
        person_name = None
        if lead.suggested_suspect.person and lead.suggested_suspect.person.full_name:
            person_name = lead.suggested_suspect.person.full_name
        suggested = LeadSuspectRef(
            name=person_name,
            suspectId=lead.suggested_suspect.suspect_id,
            basis=extras.get("Suspect basis"),
        )
    elif extras.get("Suspect basis") or extras.get("Source"):
        # Free-text suspect name was originally typed but no FK was created.
        # We don't store the typed name separately, so we leave name=None
        # and let the basis show up in the dialog.
        if extras.get("Suspect basis"):
            suggested = LeadSuspectRef(name=None, basis=extras.get("Suspect basis"))

    is_ai = (lead.event_source or "").upper() == EVENT_SOURCE_AI

    return LeadRow(
        id=lead.lead_id,
        caseId=lead.case.case_id if lead.case else "",
        eventSource="ai" if is_ai else "manual",
        type=lead.type.label if lead.type else "—",
        description=clean_desc,
        severity=lead.severity.label if lead.severity else "Medium",
        status=lead.status.label if lead.status else "New",
        confidence=lead.confidence if lead.confidence and lead.confidence > 0 else (None if not is_ai else lead.confidence),
        nextStep=lead.next_step,
        source=source,
        officerName=officer_name,
        suggestedSuspect=suggested,
        similarCaseId=lead.similar_case.case_id if lead.similar_case else None,
        generatedAt=lead.generated_at or datetime.utcnow(),
        editable=not is_ai,           # manual leads only
        dismissable=True,
    )


# ─── Triple-write helper ────────────────────────────────────────────────────

def _log_lead_action(
    db: Session, *,
    case: Case, user: User, request: Optional[Request],
    title: str, description: str, audit_action: str,
    audit_target_id: str,
    activity_type: str = "lead",
):
    """Write TimelineEvent + Activity + AuditLog for one lead mutation."""
    now = datetime.utcnow()
    db.add(TimelineEvent(
        case_id_fk=case.id,
        event_id=f"EVT-{int(now.timestamp() * 1000):X}-{secrets.token_hex(2).upper()}",
        event_source="SYSTEM",
        event_type_id=_timeline_event_type_id(db, "AI_LEAD_GENERATED"),
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
        title=title, description=description, type=activity_type,
        case_id=case.id, user_id=user.id, created_at=now,
    ))
    try:
        audit.log_event(
            db, user_id=user.id, action=audit_action, module="Case Management",
            detail=f"{title} (case {case.case_id})",
            target_type="lead", target_id=audit_target_id,
            request=request,
        )
    except Exception:
        pass


# ─── 1. List ────────────────────────────────────────────────────────────────

def list_leads(
    db: Session,
    *,
    user: User,
    case_id: str,
    request: Optional[Request],
    keyword: str = "",
    lead_type: str = "",
    severity: str = "all",
    source: str = "all",                # all | ai | manual
    date_from: str = "",                # YYYY-MM-DD
    page: int = 1,
    page_size: int = 5,
) -> CaseLeadsList:
    """Server-side filter + paginate for the Leads tab."""
    case = _resolve_case(db, user=user, case_id=case_id)

    base = (
        db.query(Lead)
        .filter(Lead.case_id_fk == case.id)
        .options(
            joinedload(Lead.type),
            joinedload(Lead.status),
            joinedload(Lead.severity),
            joinedload(Lead.case),
            joinedload(Lead.suggested_suspect),
            joinedload(Lead.similar_case),
            joinedload(Lead.created_by),
        )
    )

    q = base

    # Keyword
    kw = (keyword or "").strip()
    if kw:
        like = f"%{kw}%"
        q = q.outerjoin(LeadType, Lead.type_id == LeadType.id).filter(
            or_(
                Lead.lead_id.ilike(like),
                Lead.description.ilike(like),
                Lead.next_step.ilike(like),
                LeadType.label.ilike(like),
            )
        )

    # Type filter
    if lead_type and lead_type.strip():
        type_id = _lead_type_id(db, lead_type.strip())
        if type_id is not None:
            q = q.filter(Lead.type_id == type_id)
        else:
            # type label not found → no rows
            q = q.filter(Lead.id == -1)

    # Severity filter
    if severity and severity != "all":
        sev_id = _severity_id_by_label(db, severity)
        if sev_id is not None:
            q = q.filter(Lead.severity_id == sev_id)

    # Source filter (AI vs Manual)
    src = (source or "all").lower()
    if src == "ai":
        q = q.filter(Lead.event_source == EVENT_SOURCE_AI)
    elif src == "manual":
        q = q.filter(Lead.event_source == EVENT_SOURCE_MANUAL)

    # Date filter — leads generated on/after the given date
    if date_from:
        try:
            d = datetime.strptime(date_from, "%Y-%m-%d")
            q = q.filter(Lead.generated_at >= d)
        except ValueError:
            pass

    total = q.distinct().count()

    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    rows = (
        q.order_by(desc(Lead.generated_at), desc(Lead.id))
         .distinct()
         .limit(page_size)
         .offset((page - 1) * page_size)
         .all()
    )
    items = [_row_from_lead(l) for l in rows]

    # Counts (unfiltered by source — they go into the source-pill badges)
    base_unscoped = base
    counts = LeadCounts(
        all=base_unscoped.count(),
        ai=base_unscoped.filter(Lead.event_source == EVENT_SOURCE_AI).count(),
        manual=base_unscoped.filter(Lead.event_source == EVENT_SOURCE_MANUAL).count(),
    )

    type_options = [
        r.label for r in
        db.query(LeadType)
          .filter(LeadType.active == True)  # noqa: E712
          .order_by(LeadType.sort_order, LeadType.label)
          .all()
    ]

    try:
        audit.log_event(
            db, user_id=user.id, action="VIEW", module="Case Management",
            detail=(
                f"Viewed leads list (case={case_id}, kw='{kw}', type={lead_type}, "
                f"severity={severity}, source={src}, page={page}). "
                f"Returned {len(items)}/{total}."
            ),
            target_type="lead_list", target_id=case_id, request=request,
        )
        db.commit()
    except Exception:
        db.rollback()

    return CaseLeadsList(
        items=items, total=total, page=page, page_size=page_size,
        counts=counts, type_options=type_options,
    )


# ─── 2. Add manual lead ─────────────────────────────────────────────────────

def add_manual_lead(
    db: Session,
    *,
    user: User,
    case_id: str,
    body: AddManualLeadRequest,
    request: Optional[Request],
) -> LeadRow:
    case = _resolve_case(db, user=user, case_id=case_id)

    type_id = _lead_type_id(db, body.type)
    if type_id is None:
        raise HTTPException(
            status_code=400,
            detail=f"Lead type '{body.type}' not found in lkp_lead_types",
        )

    suspect_fk_id, _, _ = _resolve_suggested_suspect(
        db, case=case, name_or_id=body.suggestedSuspect,
    )
    similar_case_pk = _resolve_similar_case(db, case_id_str=body.similarCaseId)

    # Encode the columnless fields into the description suffix
    extras_suffix = _encode_extras(
        source=body.source,
        officer=_format_officer_name(user),
        weapon=body.weaponPattern,
        area=body.locationArea,
        suspect_basis=body.suspectBasis,
    )
    description = (body.description or "").rstrip() + extras_suffix

    lead = Lead(
        case_id_fk=case.id,
        lead_id=_next_lead_id(),
        event_source=EVENT_SOURCE_MANUAL,
        type_id=type_id,
        status_id=_lead_status_id_by_label(db, body.status or "New"),
        severity_id=_severity_id_by_label(db, body.severity),
        description=description,
        confidence=float(body.confidence) if body.confidence is not None else 0.0,
        next_step=body.nextStep or None,
        suggested_suspect_id=suspect_fk_id,
        similar_case_id=similar_case_pk,
        created_by_user_id=user.id,
    )

    try:
        db.add(lead)
        db.flush()
        _log_lead_action(
            db, case=case, user=user, request=request,
            title=f"Manual Lead Added: {body.type}",
            description=(body.description or "")[:200],
            audit_action="CREATE",
            audit_target_id=lead.lead_id,
            activity_type="lead",
        )
        db.commit()
    except HTTPException:
        db.rollback(); raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to add lead: {e}")

    db.refresh(lead)
    fresh = _resolve_lead(db, case=case, lead_id=lead.lead_id)  # rehydrate joined rels
    return _row_from_lead(fresh)


# ─── 3. Update lead status (inline <select>) ────────────────────────────────

def update_lead_status(
    db: Session,
    *,
    user: User,
    case_id: str,
    lead_id: str,
    body: UpdateLeadStatusRequest,
    request: Optional[Request],
) -> LeadRow:
    case = _resolve_case(db, user=user, case_id=case_id)
    lead = _resolve_lead(db, case=case, lead_id=lead_id)

    new_label = body.status
    if new_label not in STATUS_LABEL_TO_CODE:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown status '{new_label}'. Allowed: {list(STATUS_LABEL_TO_CODE.keys())}",
        )

    old_label = lead.status.label if lead.status else "—"
    if old_label == new_label:
        return _row_from_lead(lead)        # no-op

    new_status_id = _lead_status_id_by_label(db, new_label)
    lead.status_id = new_status_id

    try:
        _log_lead_action(
            db, case=case, user=user, request=request,
            title=f"Lead Status Changed: {lead.lead_id}",
            description=f"{old_label} → {new_label}",
            audit_action="UPDATE",
            audit_target_id=lead.lead_id,
            activity_type="update",
        )
        db.commit()
    except HTTPException:
        db.rollback(); raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to update status: {e}")

    fresh = _resolve_lead(db, case=case, lead_id=lead.lead_id)
    return _row_from_lead(fresh)


# ─── 4. Delete (manual leads only) ──────────────────────────────────────────

def delete_lead(
    db: Session,
    *,
    user: User,
    case_id: str,
    lead_id: str,
    request: Optional[Request],
) -> DeleteLeadResult:
    """Hard-delete a manual lead. AI leads cannot be deleted — they should
    be set to status='Dismissed' to preserve the audit trail."""
    case = _resolve_case(db, user=user, case_id=case_id)
    lead = _resolve_lead(db, case=case, lead_id=lead_id)

    if (lead.event_source or "").upper() == EVENT_SOURCE_AI:
        raise HTTPException(
            status_code=400,
            detail="AI-generated leads cannot be deleted. Set status to 'Dismissed' instead.",
        )

    lead_type_label = lead.type.label if lead.type else "—"
    db.delete(lead)

    try:
        _log_lead_action(
            db, case=case, user=user, request=request,
            title=f"Manual Lead Deleted: {lead_id}",
            description=f"Deleted lead of type '{lead_type_label}'.",
            audit_action="DELETE",
            audit_target_id=lead_id,
            activity_type="update",
        )
        db.commit()
    except HTTPException:
        db.rollback(); raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to delete lead: {e}")

    return DeleteLeadResult(deleted_id=lead_id)