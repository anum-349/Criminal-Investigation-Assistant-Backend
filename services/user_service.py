import base64
import os
import re
from datetime import UTC, datetime, timedelta
import secrets
from typing import Dict, Optional
from dotenv import load_dotenv
from sqlalchemy.orm import Session
from passlib.context import CryptContext
from fastapi import HTTPException, Request

from auth.jwt import create_access_token
from models import User, UserPreference, UserRole, UserRolePermission, Permission, Investigator, Admin
from services import audit_service as audit

load_dotenv()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

MAX_FAILED_LOGINS = int(os.getenv("MAX_FAILED_LOGINS", "5"))
LOCKOUT_MINUTES   = int(os.getenv("LOCKOUT_MINUTES",   "15"))
PASSWORD_MIN_LEN  = 8

UPLOADS_ROOT       = os.getenv("UPLOADS_DIR", "uploads")
UPLOADS_URL_PREFIX = os.getenv("UPLOADS_URL_PREFIX", "/uploads")
MAX_PHOTO_BYTES    = int(os.getenv("MAX_PHOTO_BYTES", str(5 * 1024 * 1024)))  # 5 MB

_DATA_URL_RE = re.compile(
    r"^data:(?P<mime>[\w/+\-.]+);base64,(?P<body>.+)$", re.DOTALL
)
_MIME_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg":  ".jpg",
    "image/png":  ".png",
    "image/webp": ".webp",
}

PREF_KEYS = {
    "email_notifications",
    "case_update_alerts",
    "ai_lead_notifications",
    "sound_alerts",
    "compact_view",
    "auto_save_drafts",
}

DEFAULTS = {
    "email_notifications":    True,
    "case_update_alerts":     True,
    "ai_lead_notifications":  True,
    "sound_alerts":           False,
    "compact_view":           False,
    "auto_save_drafts":       True,
}

ROLE_PERMISSIONS = {
    "admin": "*",
    "investigator": [
        "auth.login", "auth.password.change",
        "case.create", "case.read", "case.update", "case.assign",
        "case.status.change", "case.link",
        "person.create", "person.read", "person.update",
        "evidence.create", "evidence.read", "evidence.update",
        "lead.create", "lead.read", "lead.update", "lead.dismiss",
        "note.create", "note.read", "note.update", "note.delete",
        "timeline.create", "timeline.read", "timeline.update",
        "hotspot.read", "analytics.read",
        "report.generate", "report.export.pdf", "report.export.csv",
        "ai.analysis.run", "ai.entity.verify",
        "settings.read",
    ],
}

def _generate_badge_number(db: Session, role: str) -> str:
    last_user = db.query(User).order_by(User.id.desc()).first()
    next_number = 1 if not last_user else last_user.id + 1
    prefix = "ADMN/CIA-" if role == "admin" else "INV/CIA-"
    while True:
        badge_number = f"{prefix}{next_number:06d}"
        if not db.query(User).filter(User.badge_number == badge_number).first():
            return badge_number
        next_number += 1


def _validate_password(password: str) -> None:
    if len(password) < PASSWORD_MIN_LEN:
        raise Exception(f"Password must be at least {PASSWORD_MIN_LEN} characters.")
    if not re.search(r"[A-Z]", password):
        raise Exception("Password must contain at least one uppercase letter.")
    if not re.search(r"[a-z]", password):
        raise Exception("Password must contain at least one lowercase letter.")
    if not re.search(r"\d", password):
        raise Exception("Password must contain at least one digit.")


def _grant_default_permissions(db: Session, user_role: UserRole, role: str) -> None:
    spec = ROLE_PERMISSIONS.get(role, [])
    if spec == "*":
        permissions = db.query(Permission).all()
    else:
        permissions = db.query(Permission).filter(Permission.code.in_(spec)).all()
    for perm in permissions:
        db.add(UserRolePermission(
            user_role_id=user_role.id,
            permission_id=perm.id,
        ))



def _decode_profile_image(data_url: str):
    if not data_url:
        raise HTTPException(status_code=400, detail="Empty image payload")
    m = _DATA_URL_RE.match(data_url)
    if not m:
        raise HTTPException(status_code=400, detail="Must be a base64 data URL")
    mime = m.group("mime")
    if not mime.startswith("image/"):
        raise HTTPException(status_code=400, detail=f"Expected image, got: {mime}")
    try:
        raw = base64.b64decode(m.group("body"), validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 body")
    if len(raw) > MAX_PHOTO_BYTES:
        raise HTTPException(status_code=413, detail="Image exceeds 5 MB limit")
    return raw, mime


def upload_profile_picture(db: Session, user_id: int, data_url: str) -> str:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    raw, mime = _decode_profile_image(data_url)

    # Delete old picture from disk
    if user.picture_url:
        try:
            old_url = user.picture_url  # e.g. "/uploads/profile_pictures/42/abc123.jpg"
            prefix  = UPLOADS_URL_PREFIX.rstrip("/")  # e.g. "/uploads"

            if old_url.startswith(prefix):
                rel      = old_url[len(prefix):].lstrip("/")   # "profile_pictures/42/abc123.jpg"
                old_path = os.path.join(UPLOADS_ROOT, rel)     # "uploads/profile_pictures/42/abc123.jpg"
                if os.path.isfile(old_path):
                    os.remove(old_path)
        except Exception:
            pass  

    folder = os.path.join(UPLOADS_ROOT, "profile_pictures", str(user_id))
    os.makedirs(folder, exist_ok=True)

    ext      = _MIME_TO_EXT.get(mime, ".jpg")
    fname    = f"{secrets.token_hex(8)}{ext}"
    abs_path = os.path.join(folder, fname)
    with open(abs_path, "wb") as f:
        f.write(raw)

    rel_path    = os.path.relpath(abs_path, UPLOADS_ROOT).replace(os.sep, "/")
    picture_url = f"{UPLOADS_URL_PREFIX.rstrip('/')}/{rel_path}"

    user.picture_url = picture_url
    db.commit()
    db.refresh(user)
    return picture_url

def register_user(
    db: Session,
    username: str,
    password: str,
    role: str,
    secret_code: Optional[str],
    email: Optional[str] = None,
    request: Optional[Request] = None,
):
    if db.query(User).filter(User.username == username).first():
        raise Exception("Username already exists")
    if email and db.query(User).filter(User.email == email).first():
        raise Exception("Email already in use")
    if role not in ("admin", "investigator"):
        raise Exception("Role must be 'admin' or 'investigator'")
    if role == "admin" and secret_code != os.getenv("ADMIN_SECRET_CODE"):
        raise Exception("Invalid admin secret code")

    _validate_password(password)

    badge_number = _generate_badge_number(db, role)
    hashed_password = pwd_context.hash(password[:72])

    new_user = User(
        username=username,
        badge_number=badge_number,
        email=email or f"{username}@cia.local",
        password=hashed_password,
        role=role,
        status="active",
    )
    db.add(new_user)
    db.flush()

    if role == "admin":
        db.add(Admin(id=new_user.id, admin_level="Standard"))
    else:
        db.add(Investigator(id=new_user.id, department="", rank=""))

    user_role = UserRole(role_name=role, user_id=new_user.id)
    db.add(user_role)
    db.flush()
    _grant_default_permissions(db, user_role, role)

    audit.log_register(db, new_user, request=request)

    db.commit()
    db.refresh(new_user)

    token = create_access_token({"id": new_user.id, "role": new_user.role})
    return {
        "id": new_user.id,
        "username": new_user.username,
        "badge_number": new_user.badge_number,
        "role": new_user.role,
        "access_token": token,
        "token_type": "bearer",
    }

def login_user(
    db: Session,
    identifier: str,
    password: str,
    secret_code: Optional[str],
    request: Optional[Request] = None,
):
    """
    Login flow with full audit logging at every branch:
      • Identifier not found       → LOGIN_FAILED (user_id=NULL)
      • Account currently locked   → LOGIN_BLOCKED
      • Wrong password (not last)  → LOGIN_FAILED
      • Wrong password (5th try)   → LOGIN_FAILED + ACCOUNT_LOCKED
      • Account inactive           → LOGIN_FAILED (reason: status)
      • Wrong admin secret         → LOGIN_FAILED (reason: admin code)
      • Success                    → LOGIN_SUCCESS
    """
    db_user = (
        db.query(User)
          .filter((User.username == identifier) | (User.badge_number == identifier))
          .first()
    )
    if not db_user:
        audit.log_login_failed(db, identifier=identifier, user=None,
                                request=request, reason="Identifier not found")
        db.commit()
        raise Exception("Invalid credentials")

    if db_user.locked_until and db_user.locked_until > datetime.now(UTC):
        audit.log_login_blocked(db, db_user, request=request)
        db.commit()
        remaining = int((db_user.locked_until - datetime.now(UTC)).total_seconds() / 60) + 1
        raise Exception(f"Account locked. Try again in {remaining} minute(s).")

    if not pwd_context.verify(password, db_user.password):
        db_user.failed_login_count = (db_user.failed_login_count or 0) + 1

        if db_user.failed_login_count >= MAX_FAILED_LOGINS:
            db_user.locked_until = datetime.now(UTC) + timedelta(minutes=LOCKOUT_MINUTES)
            audit.log_login_failed(db, identifier=identifier, user=db_user,
                                    request=request, reason="Wrong password (lockout triggered)")
            audit.log_account_locked(db, db_user, db_user.locked_until, request=request)
            db.commit()
            raise Exception(
                f"Too many failed attempts. Account locked for {LOCKOUT_MINUTES} minutes."
            )

        audit.log_login_failed(db, identifier=identifier, user=db_user,
                                request=request, reason="Wrong password")
        db.commit()
        raise Exception("Invalid credentials")

    if db_user.status != "active":
        audit.log_login_failed(db, identifier=identifier, user=db_user,
                                request=request, reason=f"Account status: {db_user.status}")
        db.commit()
        raise Exception(f"Account is {db_user.status}. Contact administrator.")

    if db_user.role == "admin" and secret_code != os.getenv("ADMIN_SECRET_CODE"):
        audit.log_login_failed(db, identifier=identifier, user=db_user,
                                request=request, reason="Invalid admin secret code")
        db.commit()
        raise Exception("Invalid admin secret code")

    db_user.failed_login_count = 0
    db_user.locked_until = None
    db_user.last_login = datetime.now(UTC)
    audit.log_login_success(db, db_user, request=request)
    db.commit()

    token = create_access_token({"id": db_user.id, "role": db_user.role})
    return {
        "access_token": token,
        "token_type": "bearer",
        "id": db_user.id,
        "username": db_user.username,
        "badge_number": db_user.badge_number,
        "role": db_user.role,
    }

def logout_user(db: Session, user: User, request: Optional[Request] = None):
    """
    Server-side "logout". JWTs are stateless so we can't actually invalidate
    the token here — the client must drop it. But we DO write the audit log
    so the audit trail captures the session-end event.

    Future enhancement: maintain a token blocklist table and add the JTI
    (JWT ID) here. For now, audit-only.
    """
    audit.log_logout(db, user, request=request)
    db.commit()
    return {"message": "Logged out successfully"}

USER_FIELDS = {"email", "contact_info", "address", "picture_url"}
INVESTIGATOR_FIELDS = {"department", "rank", "shift", "specialization"}

def update_user_profile(db: Session, user_id: int, data: dict):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise Exception("User not found")
    for key, value in data.items():
        if key in USER_FIELDS and value is not None:
            setattr(user, key, value)
    db.commit()
    db.refresh(user)
    return user


def update_investigator_profile(db: Session, user_id: int, data: dict):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise Exception("User not found")
    inv = db.query(Investigator).filter(Investigator.id == user_id).first()
    if not inv:
        raise Exception("Investigator profile not found")

    for key, value in data.items():
        if key in USER_FIELDS and value is not None:
            setattr(user, key, value)
    for key, value in data.items():
        if key in INVESTIGATOR_FIELDS and value is not None:
            setattr(inv, key, value)

    db.commit()
    db.refresh(user)
    db.refresh(inv)
    return {"user": user, "investigator": inv}

def change_password(
    db: Session,
    user: User,
    current_password: str,
    new_password: str,
    request: Optional[Request] = None,
):
    if not pwd_context.verify(current_password, user.password):
        raise Exception("Current password is incorrect")

    _validate_password(new_password)
    user.password = pwd_context.hash(new_password[:72])

    audit.log_password_changed(db, user, request=request)
    db.commit()
    return {"message": "Password changed successfully"}

def fetch_investigator_profile(db: Session, user_id: int):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise Exception("User not found")
    inv = db.query(Investigator).filter(Investigator.id == user_id).first()
    if not inv:
        raise Exception("Investigator profile not found")
    return {"user": user, "investigator": inv}

def get_preferences(db: Session, user_id: int) -> Dict[str, bool]:
    rows = (
        db.query(UserPreference)
        .filter(UserPreference.user_id == user_id)
        .all()
    )
    saved = {r.pref_key: r.pref_value == "true" for r in rows}
    return {**DEFAULTS, **saved}

def save_preferences(db: Session, user_id: int, prefs: Dict[str, bool]) -> Dict[str, bool]:
    for key, value in prefs.items():
        if key not in PREF_KEYS:
            continue  
        row = (
            db.query(UserPreference)
            .filter(UserPreference.user_id == user_id, UserPreference.pref_key == key)
            .first()
        )
        if row:
            row.pref_value = "true" if value else "false"
        else:
            db.add(UserPreference(
                user_id=user_id,
                pref_key=key,
                pref_value="true" if value else "false",
            ))
    db.commit()
    return get_preferences(db, user_id)