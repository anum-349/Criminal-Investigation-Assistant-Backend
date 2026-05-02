import os
import re
from datetime import datetime, timedelta
from dotenv import load_dotenv
from sqlalchemy.orm import Session
from passlib.context import CryptContext

from auth.jwt import create_access_token
from models import User, UserRole, UserRolePermission, Permission, Investigator, Admin

load_dotenv()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ─── Config (override via env) ───────────────────────────────────────────────
MAX_FAILED_LOGINS  = int(os.getenv("MAX_FAILED_LOGINS",  "5"))
LOCKOUT_MINUTES    = int(os.getenv("LOCKOUT_MINUTES",    "15"))
PASSWORD_MIN_LEN   = 8

# Default permission codes per role. The lkp_permissions table is seeded
# by seed_lookups.py — these codes resolve to FK ids at register time.
ROLE_PERMISSIONS = {
    "admin": "*",   # all permissions
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


# ════════════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════════════

def _generate_badge_number(db: Session, role: str) -> str:
    """Generate a unique badge number based on the next user id."""
    last_user = db.query(User).order_by(User.id.desc()).first()
    next_number = 1 if not last_user else last_user.id + 1
    prefix = "ADMIN" if role == "admin" else "INV"

    while True:
        badge_number = f"{prefix}{next_number:06d}"
        if not db.query(User).filter(User.badge_number == badge_number).first():
            return badge_number
        next_number += 1


def _validate_password(password: str) -> None:
    """Enforce R3.2.1.3.1 password complexity. Raise on violation."""
    if len(password) < PASSWORD_MIN_LEN:
        raise Exception(f"Password must be at least {PASSWORD_MIN_LEN} characters.")
    if not re.search(r"[A-Z]", password):
        raise Exception("Password must contain at least one uppercase letter.")
    if not re.search(r"[a-z]", password):
        raise Exception("Password must contain at least one lowercase letter.")
    if not re.search(r"\d", password):
        raise Exception("Password must contain at least one digit.")


def _grant_default_permissions(db: Session, user_role: UserRole, role: str) -> None:
    """Attach the default permission set for `role` to this UserRole row."""
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


# ════════════════════════════════════════════════════════════════════════════
# REGISTER
# ════════════════════════════════════════════════════════════════════════════

def register_user(db: Session, username, password, role, secret_code, email=None):
    """Create a User + role-specific profile (Admin or Investigator) +
    UserRole + default permissions. Returns the same shape as login_user."""

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

    # 1. Create User
    new_user = User(
        username=username,
        badge_number=badge_number,
        email=email or f"{username}@cia.local",  # fallback so unique constraint holds
        password=hashed_password,
        role=role,
        status="active",
    )
    db.add(new_user)
    db.flush()   # populate new_user.id

    # 2. Create role-specific profile (1:1)
    if role == "admin":
        db.add(Admin(id=new_user.id, admin_level="Standard"))
    else:
        db.add(Investigator(
            id=new_user.id,
            department="",   # filled in via /investigator/profile later
            rank="",
        ))

    # 3. Create UserRole + grant permissions
    user_role = UserRole(role_name=role, user_id=new_user.id)
    db.add(user_role)
    db.flush()
    _grant_default_permissions(db, user_role, role)

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


# ════════════════════════════════════════════════════════════════════════════
# LOGIN
# ════════════════════════════════════════════════════════════════════════════

def login_user(db: Session, identifier, password, secret_code):
    """`identifier` can be either a username or badge_number — try both.
    Implements account lockout per R3.2.1.3.2: after N failed attempts the
    account is locked for LOCKOUT_MINUTES."""

    db_user = (
        db.query(User)
          .filter((User.username == identifier) | (User.badge_number == identifier))
          .first()
    )

    if not db_user:
        # Don't tell the attacker whether the username exists — generic msg.
        raise Exception("Invalid credentials")

    # Lockout check (UC9 extension 4a)
    if db_user.locked_until and db_user.locked_until > datetime.utcnow():
        remaining = int((db_user.locked_until - datetime.utcnow()).total_seconds() / 60) + 1
        raise Exception(f"Account locked. Try again in {remaining} minute(s).")

    # Password verify
    if not pwd_context.verify(password, db_user.password):
        db_user.failed_login_count = (db_user.failed_login_count or 0) + 1
        if db_user.failed_login_count >= MAX_FAILED_LOGINS:
            db_user.locked_until = datetime.utcnow() + timedelta(minutes=LOCKOUT_MINUTES)
            db.commit()
            raise Exception(
                f"Too many failed attempts. Account locked for {LOCKOUT_MINUTES} minutes."
            )
        db.commit()
        raise Exception("Invalid credentials")

    # Status / lock checks
    if db_user.status != "active":
        raise Exception(f"Account is {db_user.status}. Contact administrator.")

    if db_user.role == "admin" and secret_code != os.getenv("ADMIN_SECRET_CODE"):
        raise Exception("Invalid admin secret code")

    # Success — reset counters, stamp last_login
    db_user.failed_login_count = 0
    db_user.locked_until = None
    db_user.last_login = datetime.utcnow()
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


# ════════════════════════════════════════════════════════════════════════════
# PROFILE UPDATES
# ════════════════════════════════════════════════════════════════════════════

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
    """
    Updates BOTH the user fields (email/contact/address/picture) AND the
    investigator-specific fields (department/rank/shift/specialization)
    in a single transaction. Replaces the previous double-call pattern.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise Exception("User not found")

    inv = db.query(Investigator).filter(Investigator.id == user_id).first()
    if not inv:
        raise Exception("Investigator profile not found")

    # User fields
    for key, value in data.items():
        if key in USER_FIELDS and value is not None:
            setattr(user, key, value)

    # Investigator fields
    for key, value in data.items():
        if key in INVESTIGATOR_FIELDS and value is not None:
            setattr(inv, key, value)

    db.commit()
    db.refresh(user)
    db.refresh(inv)
    return {"user": user, "investigator": inv}