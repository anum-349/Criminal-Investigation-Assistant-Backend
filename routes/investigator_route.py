from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from datetime import datetime, timedelta
from typing import List

from db import get_db
from dependencies.auth import get_current_user
from models import User, Case, Activity, Lead
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

def _scope_query(query, user: User, model):
    """
    Restrict a query to cases the current user owns, unless the user is admin.
    Works for any model that has an `assigned_to_id` column (Case) or that
    joins to Case.
    """
    if user.role == "admin":
        return query
    if hasattr(model, "assigned_to_id"):
        return query.filter(model.assigned_to_id == user.id)
    return query


def _format_case(case: Case) -> CaseListItem:
    return CaseListItem(
        id=case.case_id,
        crime_type=case.crime_type,
        location=case.location,
        status=case.status,
        last_update=case.last_updated.strftime("%d-%b") if case.last_updated else "",
    )


def _format_activity(activity: Activity) -> ActivityItem:
    return ActivityItem(
        id=activity.id,
        title=activity.title,
        case_id=activity.case.case_id if activity.case else None,
        description=activity.description,
        type=activity.type,
        created_at=activity.created_at,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Existing welcome endpoint (kept for backwards compatibility)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/investigator")
def investigator_dashboard(user: User = Depends(get_current_user)):
    return {"msg": f"Welcome Investigator {user.username}"}


# ─────────────────────────────────────────────────────────────────────────────
# Stats
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/investigator/dashboard/stats", response_model=DashboardStats)
def get_dashboard_stats(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Five numbers that fill the StatCard row."""
    start_of_month = datetime.utcnow().replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )

    base = db.query(Case)
    base = _scope_query(base, user, Case)

    new_this_month = base.filter(Case.created_at >= start_of_month).count()
    total_active = base.filter(Case.status == "Active").count()
    missing_data = base.filter(Case.has_missing_data == 1).count()
    solved = base.filter(Case.status == "Closed").count()

    leads_q = db.query(Lead).join(Case, Lead.case_id == Case.id)
    if user.role != "admin":
        leads_q = leads_q.filter(Case.assigned_to_id == user.id)
    leads_count = leads_q.count()

    return DashboardStats(
        new_cases_this_month=new_this_month,
        total_active_cases=total_active,
        reports_with_missing_data=missing_data,
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
    """
    Active cases for the dashboard table. Default 7 rows — matches the
    current frontend layout. Pass ?limit= to override.
    """
    q = db.query(Case).filter(Case.status == "Active")
    q = _scope_query(q, user, Case)
    cases = q.order_by(desc(Case.last_updated)).limit(limit).all()
    return [_format_case(c) for c in cases]


# ─────────────────────────────────────────────────────────────────────────────
# Recent activities
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/investigator/activities", response_model=List[ActivityItem])
def get_recent_activities(
    limit: int = Query(4, ge=1, le=20),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Recent activity feed — newest first."""
    q = db.query(Activity).join(
        Case, Activity.case_id == Case.id, isouter=True
    )
    if user.role != "admin":
        # show activities on cases assigned to this investigator
        q = q.filter(
            (Case.assigned_to_id == user.id) | (Activity.user_id == user.id)
        )
    activities = q.order_by(desc(Activity.created_at)).limit(limit).all()
    return [_format_activity(a) for a in activities]


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
    Severity is computed from case count: ≥10 high, ≥5 medium, else low.
    """
    rows = (
        db.query(
            Case.location.label("city"),
            Case.province.label("province"),
            func.avg(Case.latitude).label("lat"),
            func.avg(Case.longitude).label("lng"),
            func.count(Case.id).label("cases"),
        )
        .filter(Case.status == "Active")
        .filter(Case.latitude.isnot(None))
        .filter(Case.longitude.isnot(None))
        .group_by(Case.location, Case.province)
        .all()
    )

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
    """
    Single endpoint the dashboard page can call on mount. Reduces the
    initial-render network chatter from 4 requests to 1.
    """
    return DashboardResponse(
        stats=get_dashboard_stats(db=db, user=user),
        active_cases=get_active_cases(limit=7, db=db, user=user),
        activities=get_recent_activities(limit=4, db=db, user=user),
        hotspots=get_hotspots(db=db, user=user),
    )