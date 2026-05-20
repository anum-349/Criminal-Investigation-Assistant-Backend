from datetime import UTC, datetime, date, timedelta
from typing import Optional
from calendar import month_abbr
from collections import defaultdict

from sqlalchemy import func as sa_func, extract, case as sa_case, desc
from sqlalchemy.orm import Session
from fastapi import Request

from models import (
    Case, Lead, TimelineEvent,
    Location, Province, CaseType, CaseStatus, Severity,
)
from schemas.analytics_schema import (
    MonthlyTrendItem, CrimeByTypeItem, CrimeByProvinceItem,
    StatusDistItem, SeverityTrendItem, SummaryStats, HeatmapData,
    PredictionTrendItem, ProvinceForecastItem, CrimeForecastItem,
    AnalyticsOverviewResponse, AnalyticsTrendsResponse,
    AnalyticsBreakdownResponse, AnalyticsHeatmapResponse,
    AnalyticsPredictionsResponse,
)
from services import audit_service as audit
import logging

from services import predictive_ai_service as pai
from schemas.predictive_ai_schema import PredictiveAnalyticsResponse

log = logging.getLogger(__name__)

# ── helpers ────────────────────────────────────────────────────────────────

def _date_range(date_range: str):
    """Return (date_from, date_to) for the requested range string."""
    today = date.today()
    if date_range == "Last 3 months":
        return today - timedelta(days=91), today
    if date_range == "Last 6 months":
        return today - timedelta(days=182), today
    if date_range == "This year":
        return date(today.year, 1, 1), today
    # default: Last 9 months
    return today - timedelta(days=274), today


def _month_label(year: int, month: int) -> str:
    return month_abbr[month]   # "Jan", "Feb", …


def _months_in_range(date_from: date, date_to: date):
    """Yield (year, month) tuples from date_from to date_to inclusive."""
    y, m = date_from.year, date_from.month
    while (y, m) <= (date_to.year, date_to.month):
        yield y, m
        m += 1
        if m > 12:
            m = 1
            y += 1


def _pct_change(current: int, previous: int) -> str:
    if previous == 0:
        return "+0%"
    diff = ((current - previous) / previous) * 100
    sign = "+" if diff >= 0 else ""
    return f"{sign}{diff:.0f}%"


# ── overview ───────────────────────────────────────────────────────────────

def get_overview(
    db: Session, *,
    date_range: str,
    province: str,
    crime_type: str,
    user,
    request: Optional[Request],
) -> AnalyticsOverviewResponse:

    date_from, date_to = _date_range(date_range)

    # Base case query (not deleted)
    base_q = db.query(Case).filter(
        Case.is_deleted == False,
        Case.incident_date >= date_from,
        Case.incident_date <= date_to,
    )

    # Province filter
    if province and province != "All Provinces":
        base_q = (
            base_q
            .join(Location, Location.case_id_fk == Case.id)
            .join(Province, Province.id == Location.province_id)
            .filter(Province.label == province)
        )

    # Crime type filter
    if crime_type and crime_type != "All Types":
        base_q = (
            base_q
            .join(CaseType, CaseType.id == Case.case_type_id)
            .filter(CaseType.label.ilike(f"%{crime_type}%"))
        )

    all_cases = base_q.all()

    # ── Summary stats ─────────────────────────────────────────────────────
    total_now  = len(all_cases)
    prev_from  = date_from - (date_to - date_from)
    prev_cases = db.query(Case).filter(
        Case.is_deleted == False,
        Case.incident_date >= prev_from,
        Case.incident_date < date_from,
    ).count()

    # Solved this month = cases whose status label contains "Solved"/"Closed"
    # and were updated in the current calendar month
    this_month_start = date.today().replace(day=1)
    solved_ids = {
        r.id for r in db.query(CaseStatus)
        .filter(CaseStatus.label.in_(["Solved", "Closed", "solved", "closed"]))
        .all()
    }
    solved_now = sum(
        1 for c in all_cases
        if c.case_status_id in solved_ids
        and c.updated_at
        and c.updated_at.date() >= this_month_start
    )

    # Average resolution days (closed cases only)
    closed_cases = [
        c for c in all_cases
        if c.case_status_id in solved_ids and c.closed_at and c.reporting_date
    ]
    avg_res = (
        sum((c.closed_at.date() - c.reporting_date).days for c in closed_cases)
        / len(closed_cases)
        if closed_cases else 0
    )

    # High severity
    high_sev_ids = {
        r.id for r in db.query(Severity)
        .filter(Severity.label.in_(["High", "Critical", "high", "critical"]))
        .all()
    }
    high_sev_count = sum(1 for c in all_cases if c.priority_id in high_sev_ids)

    summary = SummaryStats(
        totalCases           = total_now,
        solvedThisMonth      = solved_now,
        avgResolutionDays    = round(avg_res, 1),
        highSeverity         = high_sev_count,
        totalCasesChange     = _pct_change(total_now, prev_cases),
        solvedChange         = "+0%",   # needs previous month solved
        avgResolutionChange  = "0d",
        highSeverityChange   = "+0%",
    )

    # ── Monthly trend ────────────────────────────────────────────────────
    # Group cases by (year, month)
    case_by_month:  dict = defaultdict(int)
    solved_by_month: dict = defaultdict(int)
    for c in all_cases:
        if c.incident_date:
            key = (c.incident_date.year, c.incident_date.month)
            case_by_month[key]  += 1
            if c.case_status_id in solved_ids:
                solved_by_month[key] += 1

    # Leads per month
    leads_q = (
        db.query(
            extract("year",  Lead.generated_at).label("yr"),
            extract("month", Lead.generated_at).label("mo"),
            sa_func.count(Lead.id).label("cnt"),
        )
        .filter(
            Lead.generated_at >= datetime.combine(date_from, datetime.min.time()),
            Lead.generated_at <= datetime.combine(date_to,   datetime.max.time()),
        )
        .group_by("yr", "mo")
        .all()
    )
    leads_by_month = {(int(r.yr), int(r.mo)): r.cnt for r in leads_q}

    monthly_trend = [
        MonthlyTrendItem(
            month  = _month_label(yr, mo),
            cases  = case_by_month.get((yr, mo), 0),
            solved = solved_by_month.get((yr, mo), 0),
            leads  = leads_by_month.get((yr, mo), 0),
        )
        for yr, mo in _months_in_range(date_from, date_to)
    ]

    # ── Cases by province ────────────────────────────────────────────────
    province_q = (
        db.query(Province.label, sa_func.count(Case.id).label("cnt"))
        .join(Location, Location.province_id == Province.id)
        .join(Case,     Case.id == Location.case_id_fk)
        .filter(
            Case.is_deleted  == False,
            Case.incident_date >= date_from,
            Case.incident_date <= date_to,
        )
        .group_by(Province.label)
        .order_by(desc("cnt"))
        .all()
    )
    crime_by_province = [
        CrimeByProvinceItem(name=label, value=cnt)
        for label, cnt in province_q
    ]

    # ── Status distribution ──────────────────────────────────────────────
    status_q = (
        db.query(CaseStatus.label, sa_func.count(Case.id).label("cnt"))
        .join(Case, Case.case_status_id == CaseStatus.id)
        .filter(
            Case.is_deleted    == False,
            Case.incident_date >= date_from,
            Case.incident_date <= date_to,
        )
        .group_by(CaseStatus.label)
        .all()
    )
    status_dist = [StatusDistItem(name=label, value=cnt) for label, cnt in status_q]

    _audit(db, user, request, "Viewed analytics overview")

    return AnalyticsOverviewResponse(
        summary         = summary,
        monthlyTrend    = monthly_trend,
        crimeByProvince = crime_by_province,
        statusDist      = status_dist,
    )


# ── trends ─────────────────────────────────────────────────────────────────

def get_trends(
    db: Session, *,
    date_range: str,
    province: str,
    crime_type: str,
    user,
    request: Optional[Request],
) -> AnalyticsTrendsResponse:

    date_from, date_to = _date_range(date_range)

    # Severity buckets — map severity rank to label bucket
    sev_map: dict = {}
    for row in db.query(Severity).all():
        label_lower = (row.label or "").lower()
        if "critical" in label_lower:
            sev_map[row.id] = "critical"
        elif "high" in label_lower:
            sev_map[row.id] = "high"
        elif "medium" in label_lower or "normal" in label_lower:
            sev_map[row.id] = "medium"
        else:
            sev_map[row.id] = "low"

    cases = (
        db.query(Case)
        .filter(
            Case.is_deleted    == False,
            Case.incident_date >= date_from,
            Case.incident_date <= date_to,
        )
        .all()
    )

    sev_by_month: dict = defaultdict(lambda: defaultdict(int))
    for c in cases:
        if c.incident_date:
            key    = (c.incident_date.year, c.incident_date.month)
            bucket = sev_map.get(c.priority_id, "low")
            sev_by_month[key][bucket] += 1

    solved_ids = {
        r.id for r in db.query(CaseStatus)
        .filter(CaseStatus.label.in_(["Solved", "Closed"]))
        .all()
    }
    case_by_month   = defaultdict(int)
    solved_by_month = defaultdict(int)
    for c in cases:
        if c.incident_date:
            key = (c.incident_date.year, c.incident_date.month)
            case_by_month[key] += 1
            if c.case_status_id in solved_ids:
                solved_by_month[key] += 1

    leads_q = (
        db.query(
            extract("year",  Lead.generated_at).label("yr"),
            extract("month", Lead.generated_at).label("mo"),
            sa_func.count(Lead.id).label("cnt"),
        )
        .filter(
            Lead.generated_at >= datetime.combine(date_from, datetime.min.time()),
            Lead.generated_at <= datetime.combine(date_to,   datetime.max.time()),
        )
        .group_by("yr", "mo").all()
    )
    leads_by_month = {(int(r.yr), int(r.mo)): r.cnt for r in leads_q}

    months = list(_months_in_range(date_from, date_to))

    severity_trend = [
        SeverityTrendItem(
            month    = _month_label(yr, mo),
            critical = sev_by_month[(yr, mo)]["critical"],
            high     = sev_by_month[(yr, mo)]["high"],
            medium   = sev_by_month[(yr, mo)]["medium"],
            low      = sev_by_month[(yr, mo)]["low"],
        )
        for yr, mo in months
    ]
    monthly_trend = [
        MonthlyTrendItem(
            month  = _month_label(yr, mo),
            cases  = case_by_month.get((yr, mo), 0),
            solved = solved_by_month.get((yr, mo), 0),
            leads  = leads_by_month.get((yr, mo), 0),
        )
        for yr, mo in months
    ]

    _audit(db, user, request, "Viewed analytics trends")

    return AnalyticsTrendsResponse(
        severityTrend = severity_trend,
        monthlyTrend  = monthly_trend,
    )


# ── breakdown ──────────────────────────────────────────────────────────────

def get_breakdown(
    db: Session, *,
    date_range: str,
    province: str,
    crime_type: str,
    user,
    request: Optional[Request],
) -> AnalyticsBreakdownResponse:

    date_from, date_to   = _date_range(date_range)
    prev_from            = date_from - (date_to - date_from)

    def _count_by_type(d_from, d_to):
        q = (
            db.query(CaseType.label, sa_func.count(Case.id).label("cnt"))
            .join(Case, Case.case_type_id == CaseType.id)
            .filter(
                Case.is_deleted    == False,
                Case.incident_date >= d_from,
                Case.incident_date <= d_to,
            )
        )
        if crime_type and crime_type != "All Types":
            q = q.filter(CaseType.label.ilike(f"%{crime_type}%"))
        return {label: cnt for label, cnt in q.group_by(CaseType.label).all()}

    current  = _count_by_type(date_from, date_to)
    previous = _count_by_type(prev_from, date_from - timedelta(days=1))

    all_types = sorted(set(list(current.keys()) + list(previous.keys())))
    crime_by_type = [
        CrimeByTypeItem(
            type  = t,
            count = current.get(t, 0),
            prev  = previous.get(t, 0),
        )
        for t in all_types
    ]

    _audit(db, user, request, "Viewed analytics breakdown")

    return AnalyticsBreakdownResponse(crimeByType=crime_by_type)


# ── heatmap ────────────────────────────────────────────────────────────────

def get_heatmap(
    db: Session, *,
    date_range: str,
    user,
    request: Optional[Request],
) -> AnalyticsHeatmapResponse:

    date_from, date_to = _date_range(date_range)

    events = (
        db.query(TimelineEvent)
        .filter(
            TimelineEvent.event_date >= date_from,
            TimelineEvent.event_date <= date_to,
            TimelineEvent.event_time != None,
        )
        .all()
    )

    # day 0=Mon … 6=Sun, hour buckets [0,3,6,9,12,15,18,21]
    DAYS  = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    HOURS = [0, 3, 6, 9, 12, 15, 18, 21]

    grid = [[0] * len(HOURS) for _ in range(len(DAYS))]

    for ev in events:
        if not ev.event_date or not ev.event_time:
            continue
        try:
            dow = ev.event_date.weekday()        # 0=Mon
            hr  = int(ev.event_time.split(":")[0])
            # Find closest bucket
            bucket = max(i for i, h in enumerate(HOURS) if h <= hr)
            grid[dow][bucket] += 1
        except (ValueError, IndexError):
            continue

    _audit(db, user, request, "Viewed analytics heatmap")

    return AnalyticsHeatmapResponse(
        heatmap=HeatmapData(
            days  = DAYS,
            hours = [f"{h:02d}" for h in HOURS],
            data  = grid,
        )
    )


# ── predictions (statistical only — no ML model yet) ──────────────────────
def get_predictions(
    db: Session, *,
    user,
    request: Optional[Request],
) -> PredictiveAnalyticsResponse:
    """
    Predictive analytics endpoint.
 
    Delegates to predictive_ai_service which runs an ensemble of
    Holt-Winters Exponential Smoothing and linear regression, picks the
    model with the lower holdout MAPE per series, and additionally:
 
        - Flags anomalous historical months (IQR test)
        - Detects forecast surges (>=15% above recent baseline)
        - Clusters recent geocoded incidents into hotspot predictions (DBSCAN)
        - Returns model metadata (sample size, accuracy, model used)
 
    The response is a *superset* of the legacy AnalyticsPredictionsResponse
    so the existing Predictions tab keeps working unchanged, and new UI
    sections (Pattern Alerts, Hotspot Map, model info banner) can be
    enabled incrementally.
    """
    return pai.run_predictions(db, user=user, request=request)

# ── audit helper ───────────────────────────────────────────────────────────

def _audit(db, user, request, detail: str):
    try:
        audit.log_event(
            db, user_id=user.id, action="VIEW",
            module="Analytics", detail=detail,
            target_type="analytics", target_id="",
            request=request,
        )
        db.commit()
    except Exception:
        db.rollback()