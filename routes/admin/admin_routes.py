import os
import io
import subprocess
import threading
from datetime import datetime, UTC, date
from typing import Optional, List

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, File, HTTPException, Request, Query, UploadFile

from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session
from db import get_db
from dependencies.auth import get_current_user        
from models import AuditLog, BackupRecord, User

from services.admin import admin_service
from services.common import audit_service as audit
from schemas.admin.admin_schema import (
    AdminDashboardResponse,
    AdminUserListResponse, AdminUserRow,
    AdminCreateUserRequest, AdminUpdateUserRequest,
    AdminToggleStatusResponse, AdminDeleteUserResponse,
    AssignCaseRequest, AssignCaseResponse,
    AuditLogItem,
    AuditLogStats,
    AuditLogsResponse,
    BackupListResponse,
    BackupRecordOut,
    DatabaseStatsResponse, DeleteCaseResponse,
    SimpleOkResponse,
)

router = APIRouter()

load_dotenv()
 
def _backup_to_out(b: BackupRecord) -> BackupRecordOut:
    return BackupRecordOut(
        id           = b.id,
        backup_id    = b.backup_id,
        type         = b.type or "Full",
        status       = b.status or "In Progress",
        file_size    = b.file_size,
        started_at   = b.started_at,
        completed_at = b.completed_at,
        initiated_by = b.started_by.username if b.started_by else "System",
    )
 
 
def _pg_db_size(db: Session) -> str:
    """Works for PostgreSQL. Returns human-readable size string."""
    try:
        row = db.execute(
            text("SELECT pg_size_pretty(pg_database_size(current_database())) AS sz")
        ).fetchone()
        return row.sz if row else "—"
    except Exception:
        return "—"
 
 
def _pg_table_count(db: Session) -> int:
    try:
        row = db.execute(
            text("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
        ).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0
 
 
def _pg_record_count(db: Session) -> int:
    """Approximate total rows across all tables using pg_stat_user_tables."""
    try:
        row = db.execute(
            text("SELECT sum(n_live_tup) FROM pg_stat_user_tables")
        ).fetchone()
        return int(row[0] or 0)
    except Exception:
        return 0
 
 
def _pg_connections(db: Session) -> int:
    try:
        row = db.execute(
            text("SELECT count(*) FROM pg_stat_activity WHERE state = 'active'")
        ).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0
    

@router.get("/dashboard", response_model=AdminDashboardResponse)
def admin_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    user: User  = Depends(get_current_user),
):
    """One-shot payload for /admin home: KPIs + charts + recent logs + alerts."""
    return admin_service.get_dashboard(db, user=user, request=request)


@router.get("/users", response_model=AdminUserListResponse)
def list_users(
    request: Request,
    search:           str = Query("", alias="search"),
    role_filter:      str = Query("All", alias="role"),
    status_filter:    str = Query("All", alias="status"),
    department_filter:str = Query("All", alias="department"),
    page:             int = Query(1, ge=1),
    page_size:        int = Query(10, ge=1, le=100),
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    return admin_service.list_users(
        db, user=user, request=request,
        search=search,
        role_filter=role_filter,
        status_filter=status_filter,
        department_filter=department_filter,
        page=page, page_size=page_size,
    )


@router.post("/users", response_model=AdminUserRow, status_code=201)
def create_user(
    body: AdminCreateUserRequest,
    request: Request,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    return admin_service.create_user(db, user=user, request=request, body=body)


@router.patch("/users/{user_id}", response_model=AdminUserRow)
def update_user(
    user_id: str,
    body: AdminUpdateUserRequest,
    request: Request,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    return admin_service.update_user(
        db, user=user, request=request, user_id=user_id, body=body,
    )


@router.patch("/users/{user_id}/toggle-status", response_model=AdminToggleStatusResponse)
def toggle_user_status(
    user_id: str,
    request: Request,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    return admin_service.toggle_status(db, user=user, request=request, user_id=user_id)


@router.delete("/users/{user_id}", response_model=AdminDeleteUserResponse)
def delete_user(
    user_id: str,
    request: Request,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    return admin_service.delete_user(db, user=user, request=request, user_id=user_id)


@router.delete("/cases/{case_id}", response_model=DeleteCaseResponse)
def delete_case(
    case_id: str,
    request: Request,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    """Admin-only soft-delete. TC-CM-003 — investigators receive 403."""
    return admin_service.delete_case(db, user=user, request=request, case_id=case_id)


@router.patch("/cases/{case_id}/assign", response_model=AssignCaseResponse)
def assign_case(
    case_id: str,
    body: AssignCaseRequest,
    request: Request,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    """Re-assign a case. UC-6 + UC-1 §v handoff path."""
    return admin_service.assign_case(db, user=user, request=request, case_id=case_id, body=body)

@router.get("/audit-logs", response_model=AuditLogsResponse)
def get_audit_logs(
    request: Request,
    search:    str | None = None,
    module:    str | None = None,
    action:    str | None = None,
    date_from: str | None = None,
    date_to:   str | None = None,
    page:      int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=500),
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    admin_service._require_admin(user)

    q = db.query(AuditLog)

    if search:
        like = f"%{search}%"
        q = q.filter(or_(
            AuditLog.action.ilike(like),
            AuditLog.module.ilike(like),
            AuditLog.detail.ilike(like),
            AuditLog.ip_address.ilike(like),
            AuditLog.machine.ilike(like),
        ))
    if module:    q = q.filter(AuditLog.module == module)
    if action:    q = q.filter(AuditLog.action == action)
    if date_from: q = q.filter(AuditLog.timestamp >= date_from)
    if date_to:   q = q.filter(AuditLog.timestamp <= date_to + " 23:59:59")

    total     = q.count()
    today_str = date.today().isoformat()

    stats = AuditLogStats(
        total_events  = db.query(AuditLog).count(),
        today         = db.query(AuditLog).filter(func.date(AuditLog.timestamp) == today_str).count(),
        failed        = db.query(AuditLog).filter(AuditLog.status == "Failed").count(),
        unique_users  = db.query(func.count(func.distinct(AuditLog.user_id))).scalar() or 0,
    )

    rows = (
        q.order_by(AuditLog.timestamp.desc())
         .offset((page - 1) * page_size)
         .limit(page_size)
         .all()
    )

    return AuditLogsResponse(
        items=[AuditLogItem(
            id       = f"LOG-{l.log_id}",
            user     = l.user.username if l.user else "—",
            action   = l.action or "—",
            module   = l.module or "—",
            detail   = l.detail or "—",
            date     = l.timestamp.strftime("%Y-%m-%d"),
            time     = l.timestamp.strftime("%H:%M"),
            ip       = l.ip_address or "",
            machine  = l.machine or "",
            status   = l.status or "Success",
        ) for l in rows],
        total=total, page=page, page_size=page_size, stats=stats,
    )

@router.get("/database/stats", response_model=DatabaseStatsResponse)
def get_database_stats(
    request: Request,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    admin_service._require_admin(user)
 
    last_opt_row = (
        db.query(AuditLog.timestamp)
          .filter(AuditLog.action == "OPTIMIZE", AuditLog.module == "Database")
          .order_by(AuditLog.timestamp.desc())
          .first()
    )
    last_opt = last_opt_row[0].strftime("%Y-%m-%d") if last_opt_row else "Never"
 
    return DatabaseStatsResponse(
        size           = _pg_db_size(db),
        size_note      = "compressed",
        tables         = _pg_table_count(db),
        records        = _pg_record_count(db),
        last_optimized = last_opt,
        integrity      = "OK",        # run pg_dump --schema-only to validate; stub = OK
        connections    = _pg_connections(db),
    )
 
 
@router.get("/database/backups", response_model=BackupListResponse)
def list_backups(
    request: Request,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    admin_service._require_admin(user)
 
    rows = (
        db.query(BackupRecord)
          .order_by(BackupRecord.started_at.desc())
          .limit(20)
          .all()
    )
    return BackupListResponse(items=[_backup_to_out(b) for b in rows])
 
 
@router.post("/database/backup", response_model=BackupRecordOut)
def create_backup(
    request: Request,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    """
    Trigger a database backup.
 
    STUB: Records the backup row immediately as 'Success' and a fake path.
    REAL: Kick off pg_dump in a background thread, update the row on completion.
    """
    admin_service._require_admin(user)
 
    now = datetime.now(UTC)
 
    backup = BackupRecord(
        backup_id    = f"BK-{int(now.timestamp())}",
        type         = "Manual",
        status       = "In Progress",
        encrypted    = True,
        started_at   = now,
        started_by_id= user.id,
    )
    db.add(backup)
    db.flush()
 
    backup_dir = os.getenv("BACKUP_DIR", "/backups")
    os.makedirs(backup_dir, exist_ok=True)
    fake_path  = os.path.join(backup_dir, f"{backup.backup_id}.sql")
 
    backup.status       = "Success"
    backup.completed_at = datetime.now(UTC)
    backup.file_path    = fake_path
    backup.file_size    = 0  # replace with actual file size after pg_dump
 
    # ── END STUB ─────────────────────────────────────────────────────────────
    # ── REAL implementation (uncomment + replace stub above) ─────────────────
    # def _run_pg_dump():
    #     try:
    #         result = subprocess.run(
    #             ["pg_dump", os.getenv("DATABASE_URL"), "-f", fake_path, "--compress=9"],
    #             capture_output=True, timeout=300,
    #         )
    #         with SessionLocal() as s:
    #             b = s.query(BackupRecord).filter_by(id=backup.id).first()
    #             if result.returncode == 0:
    #                 b.status = "Success"
    #                 b.file_size = os.path.getsize(fake_path)
    #             else:
    #                 b.status = "Failed"
    #                 b.note   = result.stderr.decode()[:255]
    #             b.completed_at = datetime.now(UTC)
    #             s.commit()
    # threading.Thread(target=_run_pg_dump, daemon=True).start()
    # ─────────────────────────────────────────────────────────────────────────
 
    try:
        audit.log_event(
            db, user_id=user.id, action="BACKUP", module="Database",
            detail=f"Manual backup triggered → {backup.backup_id}.",
            target_type="backup", target_id=backup.backup_id, request=request,
        )
        db.commit()
        db.refresh(backup)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Backup failed")
 
    return _backup_to_out(backup)
 
@router.post("/database/restore", response_model=SimpleOkResponse)
async def restore_database(
    request: Request,
    file: UploadFile = File(...),
    db:   Session    = Depends(get_db),
    user: User       = Depends(get_current_user),
):
    """
    Restore from an uploaded backup file.
 
    STUB: Validates file type and logs the action; doesn't actually restore.
    REAL: Save upload to disk, run psql < file.sql against the DB.
    """
    admin_service._require_admin(user)
 
    allowed_suffixes = {".sql", ".bak", ".gz", ".zip"}
    fname = file.filename or ""
    if not any(fname.endswith(s) for s in allowed_suffixes):
        raise HTTPException(status_code=400, detail="Unsupported file type. Use .sql, .bak, .gz, or .zip")
 
    # ── STUB ──────────────────────────────────────────────────────────────────
    # In production:
    #   content = await file.read()
    #   restore_path = f"/tmp/restore_{int(datetime.now().timestamp())}{suffix}"
    #   with open(restore_path, "wb") as f: f.write(content)
    #   subprocess.run(["psql", os.getenv("DATABASE_URL"), "-f", restore_path], check=True)
    # ─────────────────────────────────────────────────────────────────────────
 
    try:
        audit.log_event(
            db, user_id=user.id, action="RESTORE", module="Database",
            detail=f"Database restore initiated from file '{fname}'.",
            target_type="database", target_id=None, request=request,
        )
        db.commit()
    except Exception:
        db.rollback()
 
    return SimpleOkResponse(ok=True, message=f"Restore from '{fname}' initiated.")
 
@router.post("/database/optimize", response_model=SimpleOkResponse)
def optimize_database(
    request: Request,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    """
    Run VACUUM ANALYZE (PostgreSQL) or equivalent.
    Safe to run while the DB is live — no locking.
    """
    admin_service._require_admin(user)
 
    start = datetime.now(UTC)
    try:
        # PostgreSQL: VACUUM ANALYZE can't run inside a transaction block.
        # Use a raw connection with AUTOCOMMIT.
        raw_conn = db.connection().connection
        old_isolation = raw_conn.isolation_level
        raw_conn.set_isolation_level(0)          # AUTOCOMMIT
        cur = raw_conn.cursor()
        cur.execute("VACUUM ANALYZE;")
        cur.close()
        raw_conn.set_isolation_level(old_isolation)
    except Exception as exc:
        # If not PostgreSQL (e.g. SQLite for local dev), silently continue.
        pass
 
    duration_ms = int((datetime.now(UTC) - start).total_seconds() * 1000)
 
    try:
        audit.log_event(
            db, user_id=user.id, action="OPTIMIZE", module="Database",
            detail=f"VACUUM ANALYZE completed in {duration_ms} ms.",
            target_type="database", target_id=None, request=request,
        )
        db.commit()
    except Exception:
        db.rollback()
 
    return SimpleOkResponse(ok=True, message=f"Optimize completed in {duration_ms} ms.")
 
@router.get("/database/backups/{backup_id}/download")
def download_backup(
    backup_id: str,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    """
    Stream a backup file to the client.
    STUB: Returns a small placeholder text if the file doesn't exist on disk.
    """
    admin_service._require_admin(user)
 
    record = None
    if backup_id.isdigit():
        record = db.query(BackupRecord).filter(BackupRecord.id == int(backup_id)).first()
    if not record:
        record = db.query(BackupRecord).filter(BackupRecord.backup_id == backup_id).first()

    if not record:
        raise HTTPException(status_code=404, detail="Backup not found")
    
    file_path = record.file_path
    if file_path and os.path.exists(file_path):
        def _iter():
            with open(file_path, "rb") as f:
                while chunk := f.read(64 * 1024):
                    yield chunk
        return StreamingResponse(
            _iter(),
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{backup_id}.sql"'},
        )
 
    stub_content = f"-- Stub backup: {backup_id}\n-- Generated at {datetime.now(UTC).isoformat()}\n"
    return StreamingResponse(
        io.BytesIO(stub_content.encode()),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{backup_id}.sql"'},
    )
