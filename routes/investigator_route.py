from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from datetime import datetime
from typing import List

from db import get_db
from dependencies.auth import get_current_user
from models import (
    User,
    Case,
    CaseStatus,
    CaseType,
    Activity,
    Lead,
    Location,
    City,
    Province,
    CompletenessReport,
    CompletenessMissingField,
)
from schemas.investigator_dahboard_schema import (
    DashboardStats,
    CaseListItem,
    ActivityItem,
    HotspotItem,
    DashboardResponse,
)

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

# Status-code groupings. Adjust if your CaseStatus.code values differ.
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

    # Location is a 1:1 child; prefer the area, fall back to display_address
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


# ─────────────────────────────────────────────────────────────────────────────
# Existing welcome ping (kept for backwards compatibility)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/investigator")
def investigator_welcome(user: User = Depends(get_current_user)):
    return {"msg": f"Welcome Investigator {user.username}"}


# ─────────────────────────────────────────────────────────────────────────────
# Stats
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# Active cases table
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# Recent activities
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# Crime hotspots (map)
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# Combined endpoint — one call for the whole dashboard
# ─────────────────────────────────────────────────────────────────────────────

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