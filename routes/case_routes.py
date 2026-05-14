from __future__ import annotations
from datetime import UTC, date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db
from dependencies.auth import get_current_investigator, get_current_user
from models import AuditLog, CaseStatus, CaseUpdateFieldChange, CaseUpdateNote, User
from schemas.case_linked_schema import CaseLinkedCasesList
from schemas.case_location_schema import CaseLocationResponse
from schemas.case_register_schema import CaseRegisterRequest, CaseRegisterResponse, FIRFileUploadRequest, FIRFileUploadResult
from schemas.case_victim_schema import (
    CaseVictimsList, VictimDetail,
    UpdateVictimRequest,
)
from schemas.user_schema import PersonPhotoDeleteResult, PersonPhotoUploadRequest, PersonPhotoUploadResult
from services import case_linked_service, case_location_service, case_register_service, case_victim_service as svc

from schemas.all_cases_schema import AllCasesRow
from schemas.case_detail_schema import AddEvidenceRequest, AddTimelineResult, AddVictimRequest, AddWitnessRequest, CaseDetailResponse, CaseStatusOut, UpdateCaseStatusRequest, UpdateCaseStatusResponse
from schemas.case_evidence_schema import CaseEvidenceList, CaseEvidenceRow, PhotoDeleteResult, PhotoUploadRequest, PhotoUploadResult, UpdateEvidenceRequest
from schemas.case_timeline_schema import AddTimelineEventRequest, CaseTimelineList, DeleteTimelineEventResult, TimelineEventRow
from schemas.case_timeline_schema import CaseTimelineList


from schemas.case_lead_schema import (
    CaseLeadsList, LeadRow, DeleteLeadResult,
    AddManualLeadRequest, UpdateLeadStatusRequest,
)
from schemas.case_suspect_schema import (
        AddSuspectRequest, CaseSuspectsList, SuspectRow, UpdateSuspectRequest,
)
from schemas.search_schema import SearchResponse
from services.all_cases_service import get_case_summary
from services.case_detail_service import add_evidence, add_suspect, add_victim, add_witness, get_case_detail, update_case_status
from services.case_evidence_service import add_photo, delete_photo, get_evidence, list_evidences, update_evidence
from services.search_service import search_all
from services.case_lead_service import list_leads, add_manual_lead, update_lead_status, delete_lead
from services.case_suspect_service import add_suspect_photo, delete_suspect_photo, list_suspects, get_suspect, update_suspect
from services import case_timeline_service as stc
from schemas.case_witness_schema import (
    CaseWitnessesList, WitnessRow,
    UpdateWitnessRequest,
)
from services import case_witness_service as swc

router = APIRouter()

@router.get(
    "/{case_id}/victims",
    response_model=CaseVictimsList,
    summary="List every victim on the case (top-card summaries)",
)
def list_victims(
    case_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_investigator),
) -> CaseVictimsList:
    return svc.list_victims(db, user=user, case_id=case_id, request=request)

@router.get(
    "/{case_id}/victims/{victim_id}",
    response_model=VictimDetail,
    summary="Full nested detail for a single victim",
)
def get_victim(
    case_id: str,
    victim_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_investigator),
) -> VictimDetail:
    return svc.get_victim(
        db, user=user, case_id=case_id, victim_id=victim_id, request=request,
    )


@router.patch(
    "/{case_id}/victims/{victim_id}",
    response_model=VictimDetail,
    summary="Update a victim record",
)
def update_victim(
    case_id: str,
    victim_id: str,
    body: UpdateVictimRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_investigator),
) -> VictimDetail:
    return svc.update_victim(
        db, user=user, case_id=case_id, victim_id=victim_id,
        body=body, request=request,
    )

@router.get(
    "/{case_id}/witnesses",
    response_model=CaseWitnessesList,
    summary="List witnesses on a case (filterable + paginated)",
)
def list_witnesses(
    case_id: str,
    request: Request,
    search: str = Query("", description="Free-text — id, name, cnic, statement, relation"),
    status: str = Query("all", description='"all"'),
    date:   str = Query("", description="YYYY-MM-DD — created_at filter"),
    page:        int = Query(1,  ge=1),
    page_size:   int = Query(5,  ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_investigator),
) -> CaseWitnessesList:
    return swc.list_witnesses(
        db, user=user, case_id=case_id, request=request,
        search=search, status_filter=status, date_filter=date,
        page=page, page_size=page_size,
    )


@router.get(
    "/{case_id}/witnesses/{witness_id}",
    response_model=WitnessRow,
    summary="Get one witness (View Details dialog)",
)
def get_witness(
    case_id: str,
    witness_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_investigator),
) -> WitnessRow:
    return swc.get_witness(
        db, user=user, case_id=case_id, witness_id=witness_id, request=request,
    )

@router.patch(
    "/{case_id}/witnesses/{witness_id}",
    response_model=WitnessRow,
    summary="Update a witness",
)
def update_witness(
    case_id: str,
    witness_id: str,
    body: UpdateWitnessRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_investigator),
) -> WitnessRow:
    return swc.update_witness(
        db, user=user, case_id=case_id, witness_id=witness_id,
        body=body, request=request,
    )

@router.post(
    "/{case_id}/witnesses/{witness_id}/photos",
    response_model=PersonPhotoUploadResult,
)
def add_witness_photo(
    case_id: str,
    witness_id: str,
    body: PersonPhotoUploadRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_investigator),
):
    return swc.add_witness_photo(
        db, user=user, case_id=case_id, witness_id=witness_id,
        body=body, request=request,
    )


@router.delete(
    "/{case_id}/witnesses/{witness_id}/photo",
    response_model=PersonPhotoDeleteResult,
)
def delete_witness_photo(
    case_id: str,
    witness_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_investigator),
):
    return swc.delete_witness_photo(
        db, user=user, case_id=case_id, witness_id=witness_id,
        request=request,
    )

@router.get(
    "/{case_id}/linked-cases",
    response_model=CaseLinkedCasesList,
    summary="List cases linked to the given case (filterable + paginated)",
)
def list_linked_cases(
    case_id: str,
    request: Request,
    search:    str            = Query("", description="Keyword: case id, title, or relation"),
    relation:  str            = Query("", description="link_type code; empty = any"),
    date_:     Optional[date] = Query(None, alias="date", description="YYYY-MM-DD"),
    status:    str            = Query("all", description="CaseStatus.label or 'all'"),
    page:      int            = Query(1, ge=1),
    page_size: int            = Query(5, ge=1, le=50),
    db:        Session        = Depends(get_db),
    user:      User           = Depends(get_current_investigator),
):
    return case_linked_service.list_linked_cases(
        db,
        user=user,
        request=request,
        case_id=case_id,
        search=search,
        relation=relation,
        on_date=date_,
        status=status,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{case_id}/location",
    response_model=CaseLocationResponse,
    summary="Get crime-scene location, security info, proximity, and nearby cases",
)
def get_case_location(
    case_id: str,
    request: Request,
    nearby_radius_km: float = Query(
        3.0,
        ge=0.1,
        le=50.0,
        description="Search radius (km) for nearby cases panel.",
    ),
    nearby_limit: int = Query(
        10,
        ge=1,
        le=50,
        description="Maximum number of nearby cases returned.",
    ),
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_investigator),
):
    return case_location_service.get_case_location(
        db,
        user=user,
        request=request,
        case_id=case_id,
        nearby_radius_km=nearby_radius_km,
        nearby_limit=nearby_limit,
    )

@router.post(
    "/{case_id}/victims/{victim_id}/photos",
    response_model=PersonPhotoUploadResult,
)
def add_victim_photo(
    case_id: str,
    victim_id: str,
    body: PersonPhotoUploadRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_investigator),
):
    """Attach a photo to one victim. Body is a base-64 data URL produced by
    FileReader on the frontend. Triple-writes timeline+activity+audit on
    success."""
    return svc.add_victim_photo(
        db, user=user, case_id=case_id, victim_id=victim_id,
        body=body, request=request,
    )


@router.delete(
    "/{case_id}/victims/{victim_id}/photo",
    response_model=PersonPhotoDeleteResult,
)
def delete_victim_photo(
    case_id: str,
    victim_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_investigator),
):
    return svc.delete_victim_photo(
        db, user=user, case_id=case_id, victim_id=victim_id,
        request=request,
    )


@router.get("/{case_id}/summary", response_model=AllCasesRow)
def get_case_row_summary(
    case_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Lightweight 'pre-fetch' for the row when the user clicks View.
    Writes a VIEW audit row scoped to the specific case.
    Returns 404 if the case doesn't exist or the user can't see it.
    """
    row = get_case_summary(db, user=user, case_id=case_id, request=request)
    if row is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return row

@router.get("/search", response_model=SearchResponse)
def global_search(
    request: Request,
    q: str = Query("", description="Free-text search query (empty = recent items)"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Unified search across cases, suspects, victims, witnesses, leads, and
    locations. Returns up to 50 rows per category. Investigators see only
    items from cases they're assigned to; admins see everything. Every
    search writes a SEARCH row to audit_logs (R3.2.1.1.5).
    """
    return search_all(db, user=user, q=q, request=request)

@router.get("/{case_id}", response_model=CaseDetailResponse)
def get_case(
    case_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Returns the case header, stats, and full timeline. Investigators see
    only their own cases; admins see everything. 404 if not found.
    Writes one VIEW audit row.
    """
    return get_case_detail(db, user=user, case_id=case_id, request=request)

@router.post("/{case_id}/suspects", 
             response_model=AddTimelineResult, status_code=201)
def post_suspect(
    case_id: str,
    body: AddSuspectRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return add_suspect(
        db, user=user, case_id=case_id,
        request=request, suspects=body.suspects,
    )

@router.post("/{case_id}/evidences", 
             response_model=AddTimelineResult, status_code=201,)
def post_evidence(
    case_id: str,
    body: AddEvidenceRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return add_evidence(
        db, user=user, case_id=case_id,
        request=request, evidences=body.evidences,
    )

@router.post("/{case_id}/victims", 
             response_model=AddTimelineResult, status_code=201,)
def post_victim(
    case_id: str,
    body: AddVictimRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return add_victim(
        db, user=user, case_id=case_id,
        request=request, victims=body.victims,
    )

@router.post("/{case_id}/witnesses", 
             response_model=AddTimelineResult, status_code=201)
def post_witness(
    case_id: str,
    body: AddWitnessRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return add_witness(
        db, user=user, case_id=case_id,
        request=request, witnesses=body.witnesses,
    )

@router.get("/{case_id}/evidences", 
            response_model=CaseEvidenceList)
def get_case_evidences(
    case_id: str,
    request: Request,
    search: str = Query(""),
    date: str = Query("", description="YYYY-MM-DD"),
    status: str = Query("all", pattern="^(all|CCTV|PHOTO|FINGERPRINT|DNA|WEAPON|DOCUMENT|DIGITAL|STATEMENT|MEDICAL|FORENSIC|PHYSICAL|OTHER)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(5, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return list_evidences(
        db, user=user, case_id=case_id, request=request,
        search=search, date_filter=date, status_filter=status,
        page=page, page_size=page_size,
    )

@router.get("/{case_id}/evidences/{evidence_id}", 
            response_model=CaseEvidenceRow)
def get_one_evidence(
    case_id: str,
    evidence_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return get_evidence(
        db, user=user, case_id=case_id,
        evidence_id=evidence_id, request=request,
    )

@router.patch("/{case_id}/evidences/{evidence_id}",
            response_model=CaseEvidenceRow)
def patch_evidence(
    case_id: str,
    evidence_id: str,
    body: UpdateEvidenceRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return update_evidence(
        db, user=user, case_id=case_id,
        evidence_id=evidence_id, body=body, request=request,
    )

@router.post("/{case_id}/evidences/{evidence_id}/photos",
             response_model=PhotoUploadResult, status_code=201)
def post_photo(
    case_id: str,
    evidence_id: str,
    body: PhotoUploadRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return add_photo(
        db, user=user, case_id=case_id,
        evidence_id=evidence_id, body=body, request=request,
    )

@router.delete(
    "/{case_id}/evidences/{evidence_id}/photos/{photo_id}",
    response_model=PhotoDeleteResult)
def remove_photo(
    case_id: str,
    evidence_id: str,
    photo_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return delete_photo(
        db, user=user, case_id=case_id,
        evidence_id=evidence_id, photo_id=photo_id, request=request,
    )

@router.get(
    "/{case_id}/leads",
    response_model=CaseLeadsList,)
def get_case_leads(
    case_id: str,
    request: Request,
    keyword: str = Query(""),
    lead_type: str = Query(""),
    severity: str = Query("all"),
    source: str = Query("all", pattern="^(all|ai|manual)$"),
    date_from: str = Query("", description="YYYY-MM-DD"),
    page: int = Query(1, ge=1),
    page_size: int = Query(5, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return list_leads(
        db, user=user, case_id=case_id, request=request,
        keyword=keyword, lead_type=lead_type, severity=severity,
        source=source, date_from=date_from,
        page=page, page_size=page_size,
    )

@router.post(
    "/{case_id}/leads",
    response_model=LeadRow, status_code=201)
def post_manual_lead(
    case_id: str,
    body: AddManualLeadRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return add_manual_lead(
        db, user=user, case_id=case_id, body=body, request=request,
    )

@router.patch(
    "/{case_id}/leads/{lead_id}",
    response_model=LeadRow)
def patch_lead_status(
    case_id: str,
    lead_id: str,
    body: UpdateLeadStatusRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return update_lead_status(
        db, user=user, case_id=case_id, lead_id=lead_id,
        body=body, request=request,
    )

@router.delete(
    "/{case_id}/leads/{lead_id}",
    response_model=DeleteLeadResult)
def remove_lead(
    case_id: str,
    lead_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return delete_lead(
        db, user=user, case_id=case_id, lead_id=lead_id, request=request,
    )

@router.get(
    "/{case_id}/suspects",
    response_model=CaseSuspectsList,)
def get_case_suspects(
    case_id: str,
    request: Request,
    search: str = Query(""),
    status: str = Query("all"),
    date: str = Query("", description="YYYY-MM-DD"),
    page: int = Query(1, ge=1),
    page_size: int = Query(5, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return list_suspects(
        db, user=user, case_id=case_id, request=request,
        search=search, status_filter=status, date_filter=date,
        page=page, page_size=page_size,
    )

@router.get(
    "/{case_id}/suspects/{suspect_id}",
    response_model=SuspectRow)
def get_one_suspect(
    case_id: str,
    suspect_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return get_suspect(
        db, user=user, case_id=case_id,
        suspect_id=suspect_id, request=request,
    )

@router.patch(
    "/{case_id}/suspects/{suspect_id}",
    response_model=SuspectRow)
def patch_suspect(
    case_id: str,
    suspect_id: str,
    body: UpdateSuspectRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return update_suspect(
        db, user=user, case_id=case_id, suspect_id=suspect_id,
        body=body, request=request,
    )

@router.post(
    "/{case_id}/suspect/{suspect_id}/photo",
    response_model=PersonPhotoUploadResult,
)
def add_witness_photo(
    case_id: str,
    suspect_id: str,
    body: PersonPhotoUploadRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_investigator),
):
    return add_suspect_photo(
        db, user=user, case_id=case_id, suspect_id=suspect_id,
        body=body, request=request,
    )


@router.delete(
    "/{case_id}/suspect/{witness_id}/photo",
    response_model=PersonPhotoDeleteResult,
)
def delete_witness_photo(
    case_id: str,
    suspect_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_investigator),
):
    return delete_suspect_photo(
        db, user=user, case_id=case_id, suspect_id=suspect_id,
        request=request,
    )

@router.get(
    "/{case_id}/timeline",
    response_model=CaseTimelineList,
    summary="List every timeline event for a case",
)
def list_timeline(
    case_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_investigator),
) -> CaseTimelineList:
    return stc.list_timeline(db, user=user, case_id=case_id, request=request)


@router.post(
    "/{case_id}/timeline",
    response_model=TimelineEventRow,
    status_code=201,
    summary="Add a manual timeline event",
)
def add_manual_event(
    case_id: str,
    body: AddTimelineEventRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_investigator),
) -> TimelineEventRow:
    return stc.add_manual_event(db, user=user, case_id=case_id, body=body, request=request)


@router.delete(
    "/{case_id}/timeline/{event_id}",
    response_model=DeleteTimelineEventResult,
    summary="Delete a manual timeline event",
)
def delete_manual_event(
    case_id: str,
    event_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_investigator),
) -> DeleteTimelineEventResult:
    return stc.delete_manual_event(
        db, user=user, case_id=case_id, event_id=event_id, request=request,
    )

@router.post(
    "",
    response_model=CaseRegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new case (FIR + case details + location + crime details).",
)
def register_case_endpoint(
    body: CaseRegisterRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CaseRegisterResponse:
    """Frontend entry point used by RegisterCasePage.handleSubmit.

    On success returns 201 with `{ case_id, fir_number, timeline_events }`.
    The frontend then runs its child-entity calls (victims/suspects/etc.)
    against `case_id` — see src/services/caseApi.js → registerCaseFull.
    """
    return case_register_service.register_case(
        db,
        user=user,
        body=body,
        request=request,
    )


# ──────────────────────────────────────────────────────────────────────────
# PATCH /api/investigator/cases/{case_id}/fir-file
# ──────────────────────────────────────────────────────────────────────────
@router.patch(
    "/{case_id}/fir-file",
    response_model=FIRFileUploadResult,
    summary="Attach (or replace) the FIR file for an existing case.",
)
def upload_fir_file_endpoint(
    case_id: str,
    body: FIRFileUploadRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FIRFileUploadResult:
    """Called by the frontend immediately after POST /cases when a FIR
    file was attached. Separate endpoint so:
      - The JSON POST body stays small.
      - Re-upload / replace works without re-submitting the whole case.
      - It mirrors the file-upload pattern already used by evidence and
        person-photo flows.
    """
    return case_register_service.upload_fir_file(
        db,
        user=user,
        case_id=case_id,
        body=body,
        request=request,
    )

@router.patch(
    "/{case_id}/status",
    response_model=UpdateCaseStatusResponse,
    summary="Update case status",
)
def patch_case_status(
    case_id: str,
    body:    UpdateCaseStatusRequest,
    request: Request,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_investigator),
) -> UpdateCaseStatusResponse:
    return update_case_status(
        db,
        user        = user,
        case_id     = case_id,
        status_code = body.status_code,   # ← was body.status_id
        note        = body.note,
        request     = request,
    )