from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi_cli.cli import app
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from datetime import datetime
from typing import List
from fastapi import Request

from db import get_db
from dependencies.auth import get_current_investigator, get_current_user
from models import (
    User,
    Case,
    CaseStatus,
    Activity,
    Lead,
    Location,
    City,
    Province,
    CompletenessReport,
    CompletenessMissingField,
)
from schemas.all_cases_schema import AllCasesResponse, AllCasesRow
from schemas.case_detail_schema import AddEvidenceRequest, AddSuspectRequest, AddTimelineResult, AddVictimRequest, AddWitnessRequest, CaseDetailResponse
from schemas.case_evidence_schema import CaseEvidenceList, CaseEvidenceRow, PhotoDeleteResult, PhotoUploadRequest, PhotoUploadResult, UpdateEvidenceRequest
from schemas.case_timeline_schema import AddTimelineEventRequest, CaseTimelineList, DeleteTimelineEventResult, TimelineEventRow
from schemas.case_timeline_schema import CaseTimelineList
from schemas.investigator_dahboard_schema import (
    DashboardStats,
    CaseListItem,
    ActivityItem,
    HotspotItem,
    DashboardResponse,
)
from schemas.case_lead_schema import (
    CaseLeadsList, LeadRow, DeleteLeadResult,
    AddManualLeadRequest, UpdateLeadStatusRequest,
)
from schemas.case_suspect_schema import (
        CaseSuspectsList, SuspectRow, UpdateSuspectRequest,
)
from schemas.search_schema import SearchResponse
from services.all_cases_service import get_case_summary, list_cases
from services.case_detail_service import add_evidence, add_suspect, add_victim, add_witness, get_case_detail
from services.case_evidence_service import add_photo, delete_photo, get_evidence, list_evidences, update_evidence
from services.search_service import search_all
from services.case_lead_service import list_leads, add_manual_lead, update_lead_status, delete_lead
from services.case_suspect_service import list_suspects, get_suspect, update_suspect
from services import case_timeline_service as svc

router = APIRouter()

ACTIVE_STATUS_CODES = ("OPEN", "UNDER_INVESTIGATION", "PENDING", "ACTIVE")
CLOSED_STATUS_CODES = ("CLOSED", "SOLVED")

def _scope_to_user(query, user: User):
    """
    Restrict a Case query to cases assigned to the current investigator.
    Admins see everything. Note: Investigator.id == User.id (1:1 PK share),
    so we filter on Case.assigned_investigator_id directly with user.id.
    """
    if user.role == "admin":
        return query
    return query.filter(Case.assigned_investigator_id == user.id)

def _format_case_row(case: Case) -> CaseListItem:
    """Build the row the active-cases table expects."""
    crime_type = case.case_type.label if case.case_type else "—"
    status_label = case.case_status.label if case.case_status else "—"

    if case.location:
        if case.location.area:
            location_str = case.location.area
        elif case.location.city and case.location.city.name:
            location_str = case.location.city.name
        else:
            location_str = case.location.display_address or "—"
    else:
        location_str = "—"

    last_update = case.updated_at.strftime("%d-%b") if case.updated_at else ""

    return CaseListItem(
        id=case.case_id,
        crime_type=crime_type,
        location=location_str,
        status=status_label,
        last_update=last_update,
    )

def _format_activity_row(activity: Activity) -> ActivityItem:
    return ActivityItem(
        id=activity.id,
        title=activity.title,
        case_id=activity.case.case_id if activity.case else None,
        description=activity.description,
        type=activity.type or "update",
        created_at=activity.created_at,
    )

@router.get("/investigator")
def investigator_welcome(user: User = Depends(get_current_user)):
    return {"msg": f"Welcome Investigator {user.username}"}

@router.get("/investigator/dashboard/stats", response_model=DashboardStats)
def get_dashboard_stats(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """The five numbers in the stat-card row."""
    start_of_month = datetime.utcnow().replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )

    # Resolve status IDs once so all four queries share the same join shape.
    active_status_ids = [
        s.id for s in db.query(CaseStatus)
        .filter(CaseStatus.code.in_(ACTIVE_STATUS_CODES))
        .all()
    ]
    closed_status_ids = [
        s.id for s in db.query(CaseStatus)
        .filter(CaseStatus.code.in_(CLOSED_STATUS_CODES))
        .all()
    ]

    base = db.query(Case).filter(Case.is_deleted == False)  # noqa: E712
    base = _scope_to_user(base, user)

    new_this_month = base.filter(Case.created_at >= start_of_month).count()
    total_active = (
        base.filter(Case.case_status_id.in_(active_status_ids)).count()
        if active_status_ids
        else 0
    )
    solved = (
        base.filter(Case.case_status_id.in_(closed_status_ids)).count()
        if closed_status_ids
        else 0
    )

    # "Reports with missing data" — count distinct cases that have at least
    # one CompletenessMissingField row.
    missing_q = (
        db.query(func.count(func.distinct(Case.id)))
        .join(CompletenessReport, CompletenessReport.case_id_fk == Case.id)
        .join(
            CompletenessMissingField,
            CompletenessMissingField.report_id == CompletenessReport.id,
        )
        .filter(Case.is_deleted == False)  # noqa: E712
    )
    if user.role != "admin":
        missing_q = missing_q.filter(Case.assigned_investigator_id == user.id)
    missing_data_count = missing_q.scalar() or 0

    # Leads — count rows on cases this user owns.
    leads_q = (
        db.query(func.count(Lead.id))
        .join(Case, Lead.case_id_fk == Case.id)
        .filter(Case.is_deleted == False)  # noqa: E712
    )
    if user.role != "admin":
        leads_q = leads_q.filter(Case.assigned_investigator_id == user.id)
    leads_count = leads_q.scalar() or 0

    return DashboardStats(
        new_cases_this_month=new_this_month,
        total_active_cases=total_active,
        reports_with_missing_data=missing_data_count,
        leads_found=leads_count,
        solved_cases=solved,
    )

@router.get("/investigator/cases/active", response_model=List[CaseListItem])
def get_active_cases(
    limit: int = Query(7, ge=1, le=50),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Active cases the dashboard table renders."""
    active_status_ids = [
        s.id for s in db.query(CaseStatus)
        .filter(CaseStatus.code.in_(ACTIVE_STATUS_CODES))
        .all()
    ]
    if not active_status_ids:
        return []

    q = (
        db.query(Case)
        .filter(Case.is_deleted == False)  # noqa: E712
        .filter(Case.case_status_id.in_(active_status_ids))
    )
    q = _scope_to_user(q, user)

    cases = q.order_by(desc(Case.updated_at)).limit(limit).all()
    return [_format_case_row(c) for c in cases]

@router.get("/investigator/activities", response_model=List[ActivityItem])
def get_recent_activities(
    limit: int = Query(4, ge=1, le=20),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Recent activity feed — newest first, scoped to this investigator."""
    q = db.query(Activity).outerjoin(Case, Activity.case_id == Case.id)
    if user.role != "admin":
        q = q.filter(
            (Case.assigned_investigator_id == user.id)
            | (Activity.user_id == user.id)
        )
    activities = q.order_by(desc(Activity.created_at)).limit(limit).all()
    return [_format_activity_row(a) for a in activities]

@router.get("/investigator/hotspots", response_model=List[HotspotItem])
def get_hotspots(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Aggregates active cases by city/province for the Leaflet map.
    Severity is computed from the count: ≥10 high, ≥5 medium, else low.
    """
    active_status_ids = [
        s.id for s in db.query(CaseStatus)
        .filter(CaseStatus.code.in_(ACTIVE_STATUS_CODES))
        .all()
    ]
    if not active_status_ids:
        return []

    q = (
        db.query(
            City.name.label("city"),
            Province.label.label("province"),
            func.avg(Location.latitude).label("lat"),
            func.avg(Location.longitude).label("lng"),
            func.count(func.distinct(Case.id)).label("cases"),
        )
        .join(Location, Location.case_id_fk == Case.id)
        .join(City, Location.city_id == City.id)
        .join(Province, Location.province_id == Province.id)
        .filter(Case.is_deleted == False)  # noqa: E712
        .filter(Case.case_status_id.in_(active_status_ids))
        .filter(Location.latitude.isnot(None))
        .filter(Location.longitude.isnot(None))
        .group_by(City.id, Province.id)
    )
    if user.role != "admin":
        q = q.filter(Case.assigned_investigator_id == user.id)

    rows = q.all()
    hotspots: List[HotspotItem] = []
    for idx, r in enumerate(rows, start=1):
        if r.cases >= 10:
            severity = "high"
        elif r.cases >= 5:
            severity = "medium"
        else:
            severity = "low"
        hotspots.append(
            HotspotItem(
                id=idx,
                city=r.city,
                province=r.province,
                lat=float(r.lat),
                lng=float(r.lng),
                severity=severity,
                cases=r.cases,
            )
        )
    return hotspots

@router.get("/investigator/dashboard", response_model=DashboardResponse)
def get_dashboard(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """One round-trip for the whole dashboard page."""
    return DashboardResponse(
        stats=get_dashboard_stats(db=db, user=user),
        active_cases=get_active_cases(limit=7, db=db, user=user),
        activities=get_recent_activities(limit=4, db=db, user=user),
        hotspots=get_hotspots(db=db, user=user),
    )

@router.get("/investigator/cases", response_model=AllCasesResponse)
def get_all_cases(
    request: Request,
    search: str = Query("", description="Free-text search"),
    status: str = Query(
        "all",
        description="Status tab: all | Active | Pending | Closed",
        pattern="^(all|Active|Pending|Closed)$",
    ),
    crime_type: str = Query("All Types"),
    severity: str = Query("All Severities"),
    sort_field: str = Query(
        "registered",
        pattern="^(title|crimeType|location|investigator|status|registered|lastUpdate)$",
    ),
    sort_dir: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Paginated, filtered, sorted list of cases for the All Cases page.
    Investigators see only cases assigned to them; admins see everything.
    Every list view writes a VIEW row to audit_logs (R3.2.1.1.5).
    """
    return list_cases(
        db,
        user=user,
        request=request,
        search=search,
        status_tab=status,
        crime_type=crime_type,
        severity=severity,
        sort_field=sort_field,
        sort_dir=sort_dir,
        page=page,
        page_size=page_size,
    )

@router.get("/investigator/cases/{case_id}/summary", response_model=AllCasesRow)
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

@router.get("/investigator/search", response_model=SearchResponse)
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

@router.get("/investigator/cases/{case_id}", response_model=CaseDetailResponse)
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

@router.post("/investigator/cases/{case_id}/suspects", 
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

@router.post("/investigator/cases/{case_id}/evidences", 
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

@router.post("/investigator/cases/{case_id}/victims", 
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

@router.post("/investigator/cases/{case_id}/witnesses", 
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

@router.get("/investigator/cases/{case_id}/evidences", 
            response_model=CaseEvidenceList)
def get_case_evidences(
    case_id: str,
    request: Request,
    search: str = Query(""),
    date: str = Query("", description="YYYY-MM-DD"),
    status: str = Query("all", pattern="^(all|analyzed|Pending|pending|pending analysis|Analyzed)$"),
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

@router.get("/investigator/cases/{case_id}/evidences/{evidence_id}", 
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

@router.patch("/investigator/cases/{case_id}/evidences/{evidence_id}",
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

@router.post("/investigator/cases/{case_id}/evidences/{evidence_id}/photos",
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
    "/investigator/cases/{case_id}/evidences/{evidence_id}/photos/{photo_id}",
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
    "/investigator/cases/{case_id}/leads",
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
    "/investigator/cases/{case_id}/leads",
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
    "/investigator/cases/{case_id}/leads/{lead_id}",
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
    "/investigator/cases/{case_id}/leads/{lead_id}",
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
    "/investigator/cases/{case_id}/suspects",
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
    "/investigator/cases/{case_id}/suspects/{suspect_id}",
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
    "/investigator/cases/{case_id}/suspects/{suspect_id}",
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

@router.get(
    "/investigator/cases/{case_id}/timeline",
    response_model=CaseTimelineList,
    summary="List every timeline event for a case",
)
def list_timeline(
    case_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_investigator),
) -> CaseTimelineList:
    return svc.list_timeline(db, user=user, case_id=case_id, request=request)


@router.post(
    "/investigator/cases/{case_id}/timeline",
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
    return svc.add_manual_event(db, user=user, case_id=case_id, body=body, request=request)


@router.delete(
    "/investigator/cases/{case_id}/timeline/{event_id}",
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
    return svc.delete_manual_event(
        db, user=user, case_id=case_id, event_id=event_id, request=request,
    )