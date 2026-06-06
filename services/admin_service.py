import os
import secrets
from collections import defaultdict
from datetime import datetime, timedelta, UTC, date
from typing import List, Optional, Dict

from sqlalchemy import or_, func as sa_func, desc, extract
from sqlalchemy.orm import Session, joinedload
from fastapi import HTTPException, Request, status as http_status
from passlib.context import CryptContext

from models import (
    User, Investigator, Admin, UserRole, AuditLog,
    Case, CaseStatus, CaseType, Severity,
    Notification, Severity as SeverityModel,
    TimelineEvent,
)
from schemas.admin.admin_schema import (
    AdminDashboardResponse, AdminStatTile, AdminMonthlyTrendItem,
    AdminCaseStatusItem, AdminUsersByRole, AdminRecentLogItem,
    AdminAlertItem,
    AdminUserRow, AdminUserListResponse,
    AdminCreateUserRequest, AdminUpdateUserRequest,
    AdminToggleStatusResponse, AdminDeleteUserResponse,
    AssignCaseRequest, AssignCaseResponse, DeleteCaseResponse,
)
from services import audit_service as audit

import logging
log = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def _require_admin(user: User):
    """Raise 403 if the caller is not an admin. Use at the top of every
    public function in this module."""
    if user.role != "admin":
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )


def _fmt_user_id(user: User) -> str:
    """USR-001 style. Derived from numeric pk."""
    return f"USR-{user.id:03d}"


def _user_to_row(user: User) -> AdminUserRow:
    """Project a User ORM row → AdminUserRow."""
    dept = None
    rank = None
    if user.investigator:
        dept = user.investigator.department or None
        rank = user.investigator.rank or None

    # Display name: rank + username if rank present, else username
    full_name = f"{rank}. {user.username}" if rank else user.username

    return AdminUserRow(
        id          = _fmt_user_id(user),
        full_name   = full_name,
        username    = user.username,
        email       = user.email or "—",
        role        = "Admin" if user.role == "admin" else "Investigator",
        phone       = user.contact_info,
        department  = dept,
        status      = "Active" if (user.status or "active").lower() == "active" else "Inactive",
        last_login  = user.last_login.strftime("%Y-%m-%d %H:%M") if user.last_login else "—",
        created     = user.created_at.strftime("%Y-%m-%d") if user.created_at else None,
    )


def _user_by_external_id(db: Session, external_id: str) -> User:
    """Accept either 'USR-001' or '1' or 'username'. Look up the User."""
    raw = (external_id or "").strip()
    if not raw:
        raise HTTPException(status_code=404, detail="User not found")

    pk: Optional[int] = None
    if raw.upper().startswith("USR-"):
        try: pk = int(raw[4:])
        except ValueError: pass
    else:
        try: pk = int(raw)
        except ValueError: pass

    q = db.query(User).options(
        joinedload(User.investigator),
        joinedload(User.admin),
    )
    user = q.filter(User.id == pk).first() if pk else None
    if not user:
        user = q.filter(User.username == raw).first()

    if not user:
        raise HTTPException(status_code=404, detail=f"User '{external_id}' not found")
    return user

def get_dashboard(
    db: Session, *,
    user: User, request: Optional[Request],
) -> AdminDashboardResponse:
    """Single-shot payload for /admin home. All counts are live."""
    _require_admin(user)

    total_cases       = db.query(Case).filter(Case.is_deleted == False).count()  # noqa: E712
    active_users      = db.query(User).filter(User.status == "active").count()
    admin_count       = db.query(User).filter(User.status == "active", User.role == "admin").count()
    inv_count         = db.query(User).filter(User.status == "active", User.role == "investigator").count()

    pending_cases = (
        db.query(sa_func.count(Case.id))
          .join(CaseStatus, Case.case_status_id == CaseStatus.id)
          .filter(Case.is_deleted == False,                              # noqa: E712
                  CaseStatus.code.in_(["PENDING", "PENDING_REVIEW"]))
          .scalar() or 0
    )

    health_value = "99.9%"

    stats = [
        AdminStatTile(
            label="Total Cases",
            value=f"{total_cases:,}",
            change=None, trend="up",
            hint="across all investigators",
        ),
        AdminStatTile(
            label="Active Users",
            value=str(active_users),
            change=f"+{active_users - 0}", trend="up",
            hint=f"{admin_count} admins · {inv_count} investigators",
        ),
        AdminStatTile(
            label="Pending Cases",
            value=str(pending_cases),
            change=None, trend="flat",
            hint="awaiting review",
        ),
        AdminStatTile(
            label="System Health",
            value=health_value,
            change="Optimal", trend="up",
            hint="uptime · 30 days",
        ),
    ]

    today  = date.today()
    months = []
    cursor = today.replace(day=1)
    for _ in range(6):
        months.append((cursor.year, cursor.month))
        if cursor.month == 1:
            cursor = cursor.replace(year=cursor.year - 1, month=12)
        else:
            cursor = cursor.replace(month=cursor.month - 1)
    months.reverse()

    case_by_month = defaultdict(int)
    rows = (
        db.query(
            extract("year",  Case.created_at).label("yr"),
            extract("month", Case.created_at).label("mo"),
            sa_func.count(Case.id).label("cnt"),
        )
        .filter(Case.is_deleted == False)                                # noqa: E712
        .group_by("yr", "mo")
        .all()
    )
    for r in rows:
        case_by_month[(int(r.yr), int(r.mo))] = r.cnt

    solved_status_ids = {
        s.id for s in
        db.query(CaseStatus).filter(CaseStatus.code.in_(["SOLVED", "CLOSED"])).all()
    }
    solved_by_month = defaultdict(int)
    if solved_status_ids:
        srows = (
            db.query(
                extract("year",  Case.updated_at).label("yr"),
                extract("month", Case.updated_at).label("mo"),
                sa_func.count(Case.id).label("cnt"),
            )
            .filter(Case.is_deleted == False,                            # noqa: E712
                    Case.case_status_id.in_(solved_status_ids))
            .group_by("yr", "mo")
            .all()
        )
        for r in srows:
            solved_by_month[(int(r.yr), int(r.mo))] = r.cnt

    MONTHS_SHORT = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    monthly_trend = [
        AdminMonthlyTrendItem(
            month  = MONTHS_SHORT[mo - 1],
            cases  = case_by_month.get((yr, mo), 0),
            solved = solved_by_month.get((yr, mo), 0),
            users  = active_users,
        )
        for yr, mo in months
    ]

    status_q = (
        db.query(CaseStatus.label, sa_func.count(Case.id).label("cnt"))
          .join(Case, Case.case_status_id == CaseStatus.id)
          .filter(Case.is_deleted == False)                              # noqa: E712
          .group_by(CaseStatus.label)
          .all()
    )
    case_status = [AdminCaseStatusItem(name=lbl, value=cnt) for lbl, cnt in status_q]

    users_by_role = [
        AdminUsersByRole(role="Investigators", count=inv_count),
        AdminUsersByRole(role="Admins",        count=admin_count),
    ]

    recent = (
        db.query(AuditLog)
          .order_by(desc(AuditLog.timestamp))
          .limit(6)
          .all()
    )
    def _rel(dt: datetime) -> str:
        if not dt: return "—"
        secs = max(1, int((datetime.now(UTC) - dt.replace(tzinfo=UTC) if dt.tzinfo is None else datetime.now(UTC) - dt).total_seconds()))
        if secs < 60:   return f"{secs}s ago"
        if secs < 3600: return f"{secs // 60} min ago"
        if secs < 86400:return f"{secs // 3600} hr ago"
        return f"{secs // 86400}d ago"

    recent_logs = [
        AdminRecentLogItem(
            id      = f"LOG-{a.id}",
            user    = a.user.username if a.user else "—",
            action  = a.action or "—",
            module  = a.module or "—",
            time    = _rel(a.timestamp),
            status  = a.status or "Success",
        )
        for a in recent
    ]

    alerts: List[AdminAlertItem] = []
    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    failed_today = (
        db.query(AuditLog)
          .filter(AuditLog.status == "Failed",
                  AuditLog.timestamp >= today_start)
          .count()
    )
    if failed_today > 0:
        alerts.append(AdminAlertItem(
            id="ALERT-FAILED",
            title=f"{failed_today} failed action(s) today",
            detail="Review the audit log for details. Possible permission errors or attacks.",
            severity="warning",
            time="today",
        ))

    unassigned = (
        db.query(Case)
          .filter(Case.is_deleted == False,                              # noqa: E712
                  Case.assigned_investigator_id.is_(None))
          .count()
    )
    if unassigned > 0:
        alerts.append(AdminAlertItem(
            id="ALERT-UNASSIGNED",
            title=f"{unassigned} unassigned case(s)",
            detail="These cases have no investigator. Assign them from the All Cases page.",
            severity="warning",
            time="now",
        ))

    if not alerts:
        alerts.append(AdminAlertItem(
            id="ALERT-OK",
            title="All systems normal",
            detail="No critical events in the last 24 hours.",
            severity="info",
            time="now",
        ))

    try:
        audit.log_event(
            db, user_id=user.id, action="VIEW", module="Dashboard",
            detail="Viewed admin dashboard.",
            target_type="dashboard", target_id=None, request=request,
        )
        db.commit()
    except Exception:
        db.rollback()

    return AdminDashboardResponse(
        stats=stats,
        monthly_trend=monthly_trend,
        case_status=case_status,
        users_by_role=users_by_role,
        recent_logs=recent_logs,
        alerts=alerts,
    )

def list_users(
    db: Session, *,
    user: User, request: Optional[Request],
    search: str = "",
    role_filter: str = "All",
    status_filter: str = "All",
    department_filter: str = "All",
    page: int = 1,
    page_size: int = 10,
) -> AdminUserListResponse:
    _require_admin(user)

    q = db.query(User).options(joinedload(User.investigator), joinedload(User.admin))

    s = (search or "").strip()
    if s:
        like = f"%{s}%"
        q = q.filter(or_(
            User.username.ilike(like),
            User.email.ilike(like),
            User.badge_number.ilike(like),
        ))

    if role_filter != "All":
        backend_role = "admin" if role_filter.lower() == "admin" else "investigator"
        q = q.filter(User.role == backend_role)

    if status_filter != "All":
        backend_status = "active" if status_filter.lower() == "active" else "inactive"
        q = q.filter(User.status == backend_status)

    if department_filter != "All":
        q = q.join(Investigator, Investigator.id == User.id, isouter=True)\
             .filter(Investigator.department == department_filter)

    total = q.count()
    page  = max(1, page)
    rows  = (
        q.order_by(desc(User.created_at))
         .limit(page_size).offset((page - 1) * page_size).all()
    )
    items = [_user_to_row(u) for u in rows]

    try:
        audit.log_event(
            db, user_id=user.id, action="VIEW", module="User Management",
            detail=f"Listed users (search='{s}', role={role_filter}, status={status_filter}, page={page}).",
            target_type="user_list", target_id=None, request=request,
        )
        db.commit()
    except Exception:
        db.rollback()

    return AdminUserListResponse(items=items, total=total, page=page, page_size=page_size)


def create_user(
    db: Session, *,
    user: User, request: Optional[Request],
    body: AdminCreateUserRequest,
) -> AdminUserRow:
    _require_admin(user)

    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(status_code=409, detail="Username already exists")
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status_code=409, detail="Email already in use")

    backend_role = "admin" if body.role == "Admin" else "investigator"
    backend_status = "active" if body.status == "Active" else "inactive"

    badge_prefix = "ADM" if backend_role == "admin" else "INV"
    count = db.query(User).filter(User.role == backend_role).count()
    badge_number = f"{badge_prefix}-{count + 1:04d}"

    new_user = User(
        username     = body.username,
        badge_number = badge_number,
        email        = body.email,
        password     = pwd_context.hash(body.password[:72]),
        role         = backend_role,
        status       = backend_status,
        contact_info = body.phone,
    )
    db.add(new_user)
    db.flush()

    if backend_role == "admin":
        db.add(Admin(id=new_user.id, admin_level="Standard"))
    else:
        db.add(Investigator(
            id=new_user.id,
            department=body.department or "",
            rank=body.rank or "",
        ))

    db.add(UserRole(role_name=backend_role, user_id=new_user.id))

    try:
        audit.log_event(
            db, user_id=user.id, action="CREATE", module="User Management",
            detail=f"Created {body.role} user '{body.username}' ({badge_number}).",
            target_type="user", target_id=_fmt_user_id(new_user),
            request=request,
        )
        db.commit()
        db.refresh(new_user)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create user")

    return _user_to_row(new_user)


def update_user(
    db: Session, *,
    user: User, request: Optional[Request],
    user_id: str,
    body: AdminUpdateUserRequest,
) -> AdminUserRow:
    _require_admin(user)
    target = _user_by_external_id(db, user_id)

    changes: List[str] = []

    if body.email is not None and body.email != target.email:
        clash = db.query(User).filter(User.email == body.email, User.id != target.id).first()
        if clash:
            raise HTTPException(status_code=409, detail="Email already in use")
        target.email = body.email
        changes.append("email")

    if body.phone is not None and body.phone != (target.contact_info or ""):
        target.contact_info = body.phone or None
        changes.append("phone")

    if body.status is not None:
        backend_status = "active" if body.status == "Active" else "inactive"
        if backend_status != target.status:
            target.status = backend_status
            changes.append(f"status → {body.status}")

    if body.password:
        target.password = pwd_context.hash(body.password[:72])
        changes.append("password reset")

    if target.investigator:
        if body.department is not None and body.department != (target.investigator.department or ""):
            target.investigator.department = body.department
            changes.append("department")
        if body.rank is not None and body.rank != (target.investigator.rank or ""):
            target.investigator.rank = body.rank
            changes.append("rank")

    try:
        audit.log_event(
            db, user_id=user.id, action="UPDATE", module="User Management",
            detail=f"Updated user '{target.username}': {', '.join(changes) or 'no-op'}.",
            target_type="user", target_id=_fmt_user_id(target),
            request=request,
        )
        db.commit()
        db.refresh(target)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Update failed")

    return _user_to_row(target)


def toggle_status(
    db: Session, *,
    user: User, request: Optional[Request],
    user_id: str,
) -> AdminToggleStatusResponse:
    _require_admin(user)
    target = _user_by_external_id(db, user_id)

    if target.id == user.id:
        raise HTTPException(status_code=400, detail="You cannot deactivate yourself.")

    target.status = "inactive" if target.status == "active" else "active"
    label = "Active" if target.status == "active" else "Inactive"

    try:
        audit.log_event(
            db, user_id=user.id, action="UPDATE", module="User Management",
            detail=f"Toggled status of '{target.username}' → {label}.",
            target_type="user", target_id=_fmt_user_id(target),
            request=request,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Status toggle failed")

    return AdminToggleStatusResponse(id=_fmt_user_id(target), status=label)


def delete_user(
    db: Session, *,
    user: User, request: Optional[Request],
    user_id: str,
) -> AdminDeleteUserResponse:
    _require_admin(user)
    target = _user_by_external_id(db, user_id)

    if target.id == user.id:
        raise HTTPException(status_code=400, detail="You cannot delete your own account.")

    if target.role == "investigator":
        active_cases = db.query(Case)\
            .filter(Case.is_deleted == False,                            # noqa: E712
                    Case.assigned_investigator_id == target.id)\
            .count()
        if active_cases > 0:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot delete this account — it has {active_cases} active assigned case(s). Re-assign or close them first.",
            )

    target_id_str = _fmt_user_id(target)
    username      = target.username

    db.delete(target)

    try:
        audit.log_event(
            db, user_id=user.id, action="DELETE", module="User Management",
            detail=f"Deleted user '{username}'.",
            target_type="user", target_id=target_id_str,
            request=request,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Delete failed")

    return AdminDeleteUserResponse(deleted=True, id=target_id_str)

def assign_case(
    db: Session, *,
    user: User, request: Optional[Request],
    case_id: str,
    body: AssignCaseRequest,
) -> AssignCaseResponse:
    """Re-assign a case to a different investigator (admin-only)."""
    _require_admin(user)

    case = (
        db.query(Case)
          .filter(Case.case_id == case_id, Case.is_deleted == False)     # noqa: E712
          .first()
    )
    if not case:
        raise HTTPException(status_code=404, detail=f"Case '{case_id}' not found")

    new_inv = (
        db.query(User)
          .options(joinedload(User.investigator))
          .filter(User.username == body.investigator_username,
                  User.role     == "investigator",
                  User.status   == "active")
          .first()
    )
    if not new_inv:
        raise HTTPException(
            status_code=404,
            detail=f"Investigator '{body.investigator_username}' not found or inactive.",
        )

    old_inv_id = case.assigned_investigator_id
    case.assigned_investigator_id = new_inv.id
    case.updated_at = datetime.now(UTC)

    now = datetime.now(UTC)
    reason = (body.reason or "Admin re-assignment.").strip()
    db.add(TimelineEvent(
        case_id_fk     = case.id,
        event_id       = f"EVT-{int(now.timestamp() * 1000):X}-{secrets.token_hex(2).upper()}",
        event_source   = "SYSTEM",
        title          = f"Case re-assigned to {new_inv.username}",
        description    = reason,
        officer_name   = user.username,
        event_date     = now.date(),
        event_time     = now.strftime("%H:%M"),
        created_at     = now,
        editable       = False,
    ))

    try:
        sev = db.query(SeverityModel).filter(SeverityModel.label == "Normal").first()
        db.add(Notification(
            notification_id = f"NOTIF-ASN-{secrets.token_hex(6).upper()}",
            user_id         = new_inv.id,
            type            = "CASE_ASSIGNED",
            severity_id     = sev.id if sev else None,
            title           = f"You've been assigned case {case.case_id}",
            message         = f"{case.case_title} (assigned by {user.username})",
            link_url        = f"/investigator/case/{case.case_id}",
            related_case_id = case.id,
            created_at      = now,
        ))
    except Exception:
        log.exception("notify_assign failed")

    try:
        audit.log_event(
            db, user_id=user.id, action="UPDATE", module="Case Management",
            detail=f"Re-assigned case {case.case_id} → {new_inv.username} (was user_id={old_inv_id}).",
            target_type="case", target_id=case.case_id,
            request=request,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Assignment failed")

    return AssignCaseResponse(
        case_id=case.case_id,
        investigator=new_inv.username,
        assigned_at=now,
    )


def delete_case(
    db: Session, *,
    user: User, request: Optional[Request],
    case_id: str,
) -> DeleteCaseResponse:
    """Soft-delete a case (admin-only). TC-CM-003."""
    _require_admin(user)

    case = (
        db.query(Case)
          .filter(Case.case_id == case_id, Case.is_deleted == False)     # noqa: E712
          .first()
    )
    if not case:
        raise HTTPException(status_code=404, detail=f"Case '{case_id}' not found")

    case.is_deleted = True
    case.updated_at = datetime.now(UTC)

    try:
        audit.log_event(
            db, user_id=user.id, action="DELETE", module="Case Management",
            detail=f"Soft-deleted case '{case_id}' ({case.case_title}).",
            target_type="case", target_id=case_id,
            request=request,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Delete failed")

    return DeleteCaseResponse(deleted=True, case_id=case_id)
