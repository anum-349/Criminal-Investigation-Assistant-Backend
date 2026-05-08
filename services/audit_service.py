import secrets
from datetime import UTC, datetime
from typing import Optional
from sqlalchemy.orm import Session
from fastapi import Request

from models import AuditLog

def _generate_log_id() -> str:
    """Generate a sortable, unique log_id like 'LOG-20260502-A3F7B2'."""
    timestamp = datetime.now(UTC).strftime("%Y%m%d")
    suffix = secrets.token_hex(3).upper()
    return f"LOG-{timestamp}-{suffix}"


def _extract_request_meta(request: Optional[Request]) -> dict:
    """
    Pull IP address + machine identifier from the Request object.

    Why request is Optional:
      Some events (e.g. ACCOUNT_LOCKED triggered by background cleanup)
      don't have an HTTP request context. We want logging to still work.
    """
    if request is None:
        return {"ip_address": None, "machine": None, "user_agent": None}

    # Behind a proxy/load balancer? X-Forwarded-For wins.
    forwarded = request.headers.get("x-forwarded-for")
    ip = forwarded.split(",")[0].strip() if forwarded else (
        request.client.host if request.client else None
    )

    # User-Agent → 60-char machine identifier (fits the column)
    user_agent = request.headers.get("user-agent", "")
    machine = user_agent[:60] if user_agent else None

    return {"ip_address": ip, "machine": machine, "user_agent": user_agent}


def _write(
    db: Session,
    *,
    user_id: Optional[int],
    action: str,
    module: str,
    detail: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    status: str = "Success",
    request: Optional[Request] = None,
) -> AuditLog:
    """
    Internal write helper. All public log_* functions funnel through this
    so we never miss a field.

    Note on commits: this function does NOT commit. The caller's transaction
    decides when to commit. This way a login that succeeds writes the audit
    log atomically with the user table update (last_login, failed_count
    reset). If the caller's transaction rolls back, the audit row goes with
    it — which is correct behaviour.
    """
    meta = _extract_request_meta(request)

    log = AuditLog(
        log_id=_generate_log_id(),
        user_id=user_id,
        action=action,
        module=module,
        detail=detail,
        target_type=target_type,
        target_id=target_id,
        ip_address=meta["ip_address"],
        machine=meta["machine"],
        status=status,
        timestamp=datetime.now(UTC),
    )
    db.add(log)
    return log

def log_login_success(db: Session, user, request: Optional[Request] = None) -> AuditLog:
    """User logged in successfully."""
    return _write(
        db,
        user_id=user.id,
        action="LOGIN_SUCCESS",
        module="Authentication",
        detail=f"User '{user.username}' (badge={user.badge_number}) logged in.",
        target_type="user",
        target_id=str(user.id),
        status="Success",
        request=request,
    )


def log_login_failed(
    db: Session,
    identifier: str,
    user=None,
    request: Optional[Request] = None,
    reason: str = "Invalid credentials",
) -> AuditLog:
    """
    Failed login attempt. Account NOT yet locked.

    `user` is optional — when the identifier didn't match anyone, we still
    log the attempt (so we can detect username-enumeration attacks) but
    user_id stays NULL.
    """
    return _write(
        db,
        user_id=user.id if user else None,
        action="LOGIN_FAILED",
        module="Authentication",
        detail=f"Failed login attempt for identifier='{identifier}'. Reason: {reason}.",
        target_type="user" if user else None,
        target_id=str(user.id) if user else None,
        status="Failed",
        request=request,
    )


def log_account_locked(
    db: Session,
    user,
    locked_until: datetime,
    request: Optional[Request] = None,
) -> AuditLog:
    """
    Failed attempt that pushed the user over the lockout threshold.
    This is the critical security event — admins should monitor for it.
    R3.2.1.3.2 suspicious-login alerts feed off this row.
    """
    return _write(
        db,
        user_id=user.id,
        action="ACCOUNT_LOCKED",
        module="Authentication",
        detail=(
            f"Account '{user.username}' locked after {user.failed_login_count} "
            f"failed attempts. Locked until {locked_until.isoformat()}."
        ),
        target_type="user",
        target_id=str(user.id),
        status="Failed",
        request=request,
    )


def log_login_blocked(
    db: Session,
    user,
    request: Optional[Request] = None,
) -> AuditLog:
    """
    Attempted login while the account is currently locked.
    Tells you if an attacker keeps hammering after lockout.
    """
    return _write(
        db,
        user_id=user.id,
        action="LOGIN_BLOCKED",
        module="Authentication",
        detail=(
            f"Login attempt blocked — account '{user.username}' is locked "
            f"until {user.locked_until.isoformat() if user.locked_until else 'unknown'}."
        ),
        target_type="user",
        target_id=str(user.id),
        status="Failed",
        request=request,
    )


def log_logout(db: Session, user, request: Optional[Request] = None) -> AuditLog:
    """User explicitly logged out."""
    return _write(
        db,
        user_id=user.id,
        action="LOGOUT",
        module="Authentication",
        detail=f"User '{user.username}' logged out.",
        target_type="user",
        target_id=str(user.id),
        status="Success",
        request=request,
    )


def log_password_changed(db: Session, user, request: Optional[Request] = None) -> AuditLog:
    """User changed their own password."""
    return _write(
        db,
        user_id=user.id,
        action="PASSWORD_CHANGED",
        module="Authentication",
        detail=f"User '{user.username}' changed their password.",
        target_type="user",
        target_id=str(user.id),
        status="Success",
        request=request,
    )


def log_register(db: Session, user, request: Optional[Request] = None) -> AuditLog:
    """New user account created (self-register or admin-created)."""
    return _write(
        db,
        user_id=user.id,
        action="REGISTER",
        module="Authentication",
        detail=f"New {user.role} account '{user.username}' (badge={user.badge_number}) created.",
        target_type="user",
        target_id=str(user.id),
        status="Success",
        request=request,
    )

def log_event(
    db: Session,
    *,
    user_id: Optional[int],
    action: str,
    module: str,
    detail: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    status: str = "Success",
    request: Optional[Request] = None,
) -> AuditLog:
    """
    Generic audit logger for non-authentication events
    (CASE_CREATE, EVIDENCE_UPLOAD, BACKUP_RUN, etc.). Same shape as the
    auth helpers — keeps the audit table consistent across all modules.
    """
    return _write(
        db,
        user_id=user_id,
        action=action,
        module=module,
        detail=detail,
        target_type=target_type,
        target_id=target_id,
        status=status,
        request=request,
    )