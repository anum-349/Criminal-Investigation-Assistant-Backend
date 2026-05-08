from typing import List, Optional
from sqlalchemy import or_, asc, desc
from sqlalchemy.orm import Session, joinedload, aliased
from fastapi import Request

from models import (
    User, Investigator,
    Case, CaseStatus, CaseType,
    Severity,
    Location, City
)
from services import audit_service as audit
from schemas.all_cases_schema import (
    AllCasesRow,
    TabCounts,
    FilterOptions,
    AllCasesResponse,
)


# ─── Lookup-code groups ─────────────────────────────────────────────────────
# If your seed data uses different CaseStatus.code values, edit these tuples.
ACTIVE_STATUS_CODES  = ("OPEN", "UNDER_INVESTIGATION", "PENDING", "ACTIVE")
CLOSED_STATUS_CODES  = ("CLOSED", "SOLVED")
PENDING_STATUS_CODES = ("PENDING",)

TAB_TO_CODES = {
    "Active":  ACTIVE_STATUS_CODES,
    "Pending": PENDING_STATUS_CODES,
    "Closed":  CLOSED_STATUS_CODES,
}


# ─── Internal helpers ───────────────────────────────────────────────────────

def _scope_to_user(query, user: User):
    """Investigators see only their cases. Admins see everything."""
    if user.role == "admin":
        return query
    return query.filter(Case.assigned_investigator_id == user.id)


def _format_investigator_name(case: Case) -> str:
    """Display string for the 'Investigator' column."""
    inv = case.assigned_to
    if not inv or not inv.user:
        return "—"
    rank = (inv.rank or "").strip()
    name = inv.user.username
    return f"{rank}. {name}" if rank else name


def _format_location(case: Case) -> str:
    """Best-effort 'Area, City' string."""
    loc = case.location
    if not loc:
        return "—"
    parts = []
    if loc.area:
        parts.append(loc.area)
    if loc.city and loc.city.name:
        parts.append(loc.city.name)
    if parts:
        return ", ".join(parts)
    return loc.display_address or loc.full_address or "—"


def _row_from_case(case: Case) -> AllCasesRow:
    return AllCasesRow(
        id=case.case_id,
        title=case.case_title,
        crime_type=case.case_type.label if case.case_type else "—",
        location=_format_location(case),
        investigator=_format_investigator_name(case),
        complainant=case.complainant_name,
        fir_id=case.fir_number,
        status=case.case_status.label if case.case_status else "—",
        severity=case.priority.label if case.priority else "—",
        registered=case.created_at.strftime("%Y-%m-%d") if case.created_at else "",
        last_update=case.updated_at.strftime("%Y-%m-%d") if case.updated_at else "",
    )


def _apply_common_joins_and_filters(
    db: Session,
    *,
    user: User,
    search: str,
    status_tab: str,
    crime_type: str,
    severity: str,
    inv_user_alias,
):
    """
    Build the base query with ALL joins it might need, ONCE, in a controlled
    order. Doing every join up-front avoids 'table already aliased' errors
    no matter which filters are turned on.
    """
    q = (
        db.query(Case)
        .filter(Case.is_deleted == False)  # noqa: E712
        .outerjoin(Case.location)
        .outerjoin(Location.city)
        .outerjoin(Case.assigned_to)
        .outerjoin(inv_user_alias, Investigator.user)
        .options(
            joinedload(Case.case_type),
            joinedload(Case.case_status),
            joinedload(Case.priority),
            joinedload(Case.assigned_to).joinedload(Investigator.user),
            joinedload(Case.location).joinedload(Location.city),
            joinedload(Case.location).joinedload(Location.province),
        )
    )
    q = _scope_to_user(q, user)

    # Status tab → CaseStatus.code
    if status_tab and status_tab != "all":
        codes = TAB_TO_CODES.get(status_tab)
        if codes:
            q = q.join(CaseStatus, Case.case_status_id == CaseStatus.id)
            q = q.filter(CaseStatus.code.in_(codes))

    # Crime type by label
    if crime_type and crime_type != "All Types":
        q = q.join(CaseType, Case.case_type_id == CaseType.id)
        q = q.filter(CaseType.label == crime_type)

    # Severity by label
    if severity and severity != "All Severities":
        q = q.join(Severity, Case.priority_id == Severity.id)
        q = q.filter(Severity.label == severity)

    # Free-text search
    s = (search or "").strip()
    if s:
        like = f"%{s}%"
        q = q.filter(
            or_(
                Case.case_id.ilike(like),
                Case.case_title.ilike(like),
                Case.fir_number.ilike(like),
                Case.complainant_name.ilike(like),
                City.name.ilike(like),
                Location.area.ilike(like),
                inv_user_alias.username.ilike(like),
            )
        )

    return q


# ─── Public service methods ─────────────────────────────────────────────────

def list_cases(
    db: Session,
    *,
    user: User,
    request: Optional[Request],
    search: str = "",
    status_tab: str = "all",
    crime_type: str = "All Types",
    severity: str = "All Severities",
    sort_field: str = "registered",
    sort_dir: str = "desc",
    page: int = 1,
    page_size: int = 10,
) -> AllCasesResponse:
    """Returns a complete payload the React page can render in one shot."""

    inv_user = aliased(User)

    q = _apply_common_joins_and_filters(
        db,
        user=user,
        search=search,
        status_tab=status_tab,
        crime_type=crime_type,
        severity=severity,
        inv_user_alias=inv_user,
    )

    # Total — works on SQLite/MySQL/Postgres (unlike distinct(column))
    total = q.distinct().count()

    # Sort
    direction = desc if sort_dir == "desc" else asc

    if sort_field == "registered":
        q = q.order_by(direction(Case.created_at), direction(Case.id))
    elif sort_field == "lastUpdate":
        q = q.order_by(direction(Case.updated_at), direction(Case.id))
    elif sort_field == "title":
        q = q.order_by(direction(Case.case_title), direction(Case.id))
    elif sort_field == "crimeType":
        q = q.outerjoin(CaseType, Case.case_type_id == CaseType.id) \
             .order_by(direction(CaseType.label), direction(Case.id))
    elif sort_field == "status":
        q = q.outerjoin(CaseStatus, Case.case_status_id == CaseStatus.id) \
             .order_by(direction(CaseStatus.label), direction(Case.id))
    elif sort_field == "location":
        q = q.order_by(direction(City.name), direction(Case.id))
    elif sort_field == "investigator":
        q = q.order_by(direction(inv_user.username), direction(Case.id))
    else:
        q = q.order_by(direction(Case.created_at), direction(Case.id))

    # Paginate
    page = max(1, page)
    page_size = max(1, min(page_size, 100))

    rows = (
        q.distinct()
         .limit(page_size)
         .offset((page - 1) * page_size)
         .all()
    )
    items = [_row_from_case(c) for c in rows]

    tab_counts = _compute_tab_counts(
        db, user=user,
        search=search, crime_type=crime_type, severity=severity,
    )

    filter_options = FilterOptions(
        crime_types=_active_crime_types(db),
        severities=_active_severities(db),
    )

    # Audit — never let a logging failure break the response
    try:
        audit.log_event(
            db,
            user_id=user.id,
            action="VIEW",
            module="Case Management",
            detail=(
                f"Viewed All Cases (tab={status_tab}, crime_type={crime_type}, "
                f"severity={severity}, search='{search}', page={page}, "
                f"sort={sort_field} {sort_dir}). Returned {len(items)}/{total}."
            ),
            target_type="case_list",
            target_id=None,
            request=request,
        )
        db.commit()
    except Exception:
        db.rollback()

    return AllCasesResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        tab_counts=tab_counts,
        filter_options=filter_options,
    )


def get_case_summary(
    db: Session,
    *,
    user: User,
    case_id: str,
    request: Optional[Request],
) -> Optional[AllCasesRow]:
    """Used when the user clicks a row's 'View' button."""
    case = (
        db.query(Case)
        .filter(Case.case_id == case_id, Case.is_deleted == False)  # noqa: E712
        .options(
            joinedload(Case.case_type),
            joinedload(Case.case_status),
            joinedload(Case.priority),
            joinedload(Case.assigned_to).joinedload(Investigator.user),
            joinedload(Case.location).joinedload(Location.city),
        )
        .first()
    )
    if not case:
        return None

    if user.role != "admin" and case.assigned_investigator_id != user.id:
        try:
            audit.log_event(
                db, user_id=user.id, action="VIEW", module="Case Management",
                detail=f"Denied: case '{case_id}' not assigned to user.",
                target_type="case", target_id=case_id,
                status="Failed", request=request,
            )
            db.commit()
        except Exception:
            db.rollback()
        return None

    try:
        audit.log_event(
            db, user_id=user.id, action="VIEW", module="Case Management",
            detail=f"Viewed case '{case_id}' from All Cases list.",
            target_type="case", target_id=case_id, request=request,
        )
        db.commit()
    except Exception:
        db.rollback()

    return _row_from_case(case)


# ─── Tab counts + filter-option helpers ─────────────────────────────────────

def _compute_tab_counts(
    db: Session,
    *,
    user: User,
    search: str,
    crime_type: str,
    severity: str,
) -> TabCounts:
    """Each count reflects the user's other filters but ignores the tab."""

    def base():
        inv_user = aliased(User)
        return _apply_common_joins_and_filters(
            db,
            user=user,
            search=search,
            status_tab="all",     # ignore the tab
            crime_type=crime_type,
            severity=severity,
            inv_user_alias=inv_user,
        )

    total_all = base().distinct().count()

    def count_for(codes):
        if not codes:
            return 0
        q = base().join(CaseStatus, Case.case_status_id == CaseStatus.id)
        q = q.filter(CaseStatus.code.in_(codes))
        return q.distinct().count()

    return TabCounts(
        all=total_all,
        Active=count_for(ACTIVE_STATUS_CODES),
        Pending=count_for(PENDING_STATUS_CODES),
        Closed=count_for(CLOSED_STATUS_CODES),
    )


def _active_crime_types(db: Session) -> List[str]:
    rows = (
        db.query(CaseType.label)
        .filter(CaseType.active == True)  # noqa: E712
        .order_by(CaseType.sort_order, CaseType.label)
        .all()
    )
    return [r[0] for r in rows]


def _active_severities(db: Session) -> List[str]:
    rows = (
        db.query(Severity.label)
        .order_by(Severity.rank.desc())   # Critical → High → Medium → Low
        .all()
    )
    return [r[0] for r in rows]