from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from db import get_db
from dependencies.auth import get_current_investigator
from models import User
from schemas.case_linked_schema import CaseLinkedCasesList
from schemas.case_location_schema import CaseLocationResponse
from schemas.case_victim_schema import (
    CaseVictimsList, VictimDetail,
    UpdateVictimRequest,
)
from services import case_linked_service, case_location_service, case_victim_service as svc

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

# ─── GET /witnesses  (table) ────────────────────────────────────────────────

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


# ─── GET /witnesses/{witness_id} ────────────────────────────────────────────

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


# ─── PATCH /witnesses/{witness_id}  (Update dialog) ─────────────────────────

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