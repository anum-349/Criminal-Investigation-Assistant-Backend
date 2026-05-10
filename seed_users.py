from datetime import datetime
from passlib.context import CryptContext

from db import session_scope
from models import (
    User, Investigator, Admin,
    UserRole, UserRolePermission, Permission,
)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(plain: str) -> str:
    """Mirror of auth.py's hashing call. The [:72] slice is bcrypt's
    hard limit — passwords longer than 72 bytes are silently truncated
    by bcrypt anyway, so we cap explicitly to avoid the warning."""
    return pwd_context.hash(plain[:72])


# ════════════════════════════════════════════════════════════════════════════
# USER DEFINITIONS
# ────────────────────────────────────────────────────────────────────────────
# Two starter accounts. Edit username/password/badge here before first run
# if you want different credentials.
#
# ⚠️  CHANGE THESE PASSWORDS BEFORE DEPLOYMENT. They're set to
# easy-to-remember demo values for FYP testing only.
# ════════════════════════════════════════════════════════════════════════════

USERS = [
    {
        # ── 1. Admin user ───────────────────────────────────────────────────
        "username":      "nadia.admin",
        "email":         "nadia.admin@cia.gov.pk",
        "password":      "Admin@123",
        "role":          "admin",
        "badge_number":  "ADM/CIA-0001",
        "contact_info":  "+92-300-1112233",
        "address":       "Police HQ, Islamabad",
        "status":        "active",

        # Role-specific extra fields (admin profile)
        "admin_level":   "Super Admin",
    },
    {
        # ── 2. Investigator user ────────────────────────────────────────────
        "username":      "wajdan.mustafa",
        "email":         "wajdan.mustafa@cia.gov.pk",
        "password":      "Wajdan@123",
        "role":          "investigator",
        "badge_number":  "INV/CIA-0001",
        "contact_info":  "+92-300-4445566",
        "address":       "Clifton Police Station, Karachi",
        "status":        "active",

        # Role-specific extra fields (investigator profile)
        "department":     "Criminal Investigation Department",
        "rank":           "Inspector",
        "shift":          "Day",
        "specialization": "Homicide & Robbery",
    },
]


# ════════════════════════════════════════════════════════════════════════════
# ROLE → PERMISSIONS MAPPING
# ────────────────────────────────────────────────────────────────────────────
# Admin gets EVERY permission (the "*" wildcard below resolves to all rows
# in lkp_permissions). Investigator gets only the permissions they need to
# do their job — case management, leads, notes, evidence read/create, etc.
# Anything destructive (delete users, restore backups) is admin-only.
# ════════════════════════════════════════════════════════════════════════════

ROLE_PERMISSIONS = {
    "admin": ["*"],   # all permissions

    "investigator": [
        # Authentication
        "auth.login",
        "auth.password.change",

        # Case management — full lifecycle except delete
        "case.create", "case.read", "case.update",
        "case.assign", "case.status.change", "case.link",

        # Persons / Suspects / Victims / Witnesses — full
        "person.create", "person.read", "person.update",

        # Evidence — add and view, but no delete (chain-of-custody integrity)
        "evidence.create", "evidence.read", "evidence.update",

        # Leads — full
        "lead.create", "lead.read", "lead.update", "lead.dismiss",

        # Notes — full (own notes)
        "note.create", "note.read", "note.update", "note.delete",

        # Timeline — full
        "timeline.create", "timeline.read", "timeline.update",

        # Visualization
        "hotspot.read", "analytics.read",

        # Reports
        "report.generate", "report.export.pdf", "report.export.csv",

        # AI
        "ai.analysis.run", "ai.entity.verify",

        # Settings — read only
        "settings.read",
    ],
}


# ════════════════════════════════════════════════════════════════════════════
# Helper: create or update one user with their role/profile/permissions
# ════════════════════════════════════════════════════════════════════════════

def create_user(db, user_def: dict) -> tuple[User, bool]:
    """
    Create the User row plus the matching role-specific profile (Admin or
    Investigator) plus the UserRole and UserRolePermission rows.

    Returns (user, created_flag). created_flag=False means the username
    already existed and we skipped insertion entirely.
    """
    existing = db.query(User).filter_by(username=user_def["username"]).first()
    if existing:
        return existing, False

    # ── 1. User row ─────────────────────────────────────────────────────────
    user = User(
        username     = user_def["username"],
        email        = user_def["email"],
        password     = hash_password(user_def["password"]),
        role         = user_def["role"],
        badge_number = user_def["badge_number"],
        contact_info = user_def.get("contact_info"),
        address      = user_def.get("address"),
        status       = user_def.get("status", "active"),
        created_at   = datetime.utcnow(),
        updated_at   = datetime.utcnow(),
    )
    db.add(user)
    db.flush()   # populate user.id so we can FK-link below

    # ── 2. Role-specific profile (1:1 child row) ────────────────────────────
    if user_def["role"] == "admin":
        db.add(Admin(
            id          = user.id,
            admin_level = user_def.get("admin_level", "Standard"),
        ))
    elif user_def["role"] == "investigator":
        db.add(Investigator(
            id             = user.id,
            department     = user_def.get("department", ""),
            rank           = user_def.get("rank", ""),
            shift          = user_def.get("shift"),
            specialization = user_def.get("specialization"),
        ))

    # ── 3. UserRole row ─────────────────────────────────────────────────────
    user_role = UserRole(
        role_name = user_def["role"],
        user_id   = user.id,
    )
    db.add(user_role)
    db.flush()

    # ── 4. UserRolePermission rows ──────────────────────────────────────────
    perm_codes = ROLE_PERMISSIONS.get(user_def["role"], [])
    if perm_codes == ["*"]:
        # Admin: grant every permission in the table
        permissions = db.query(Permission).all()
    else:
        permissions = (
            db.query(Permission)
              .filter(Permission.code.in_(perm_codes))
              .all()
        )

    # Sanity check — surface any permission codes that didn't resolve, so a
    # typo in ROLE_PERMISSIONS doesn't silently grant fewer rights.
    found_codes = {p.code for p in permissions}
    if perm_codes != ["*"]:
        missing = set(perm_codes) - found_codes
        if missing:
            raise RuntimeError(
                f"Permission codes not found in lkp_permissions: {missing}\n"
                "→ Run seed_lookups.py before this script."
            )

    for perm in permissions:
        db.add(UserRolePermission(
            user_role_id  = user_role.id,
            permission_id = perm.id,
        ))

    return user, True


# ════════════════════════════════════════════════════════════════════════════
# Main entry point
# ════════════════════════════════════════════════════════════════════════════

def seed_users():
    print("\n=== Creating starter user accounts ===\n")

    with session_scope() as db:
        # Quick sanity check: did seed_lookups.py run? Without permissions
        # in place we can't grant anything to roles.
        if db.query(Permission).count() == 0:
            raise RuntimeError(
                "lkp_permissions is empty. Run `python seed_lookups.py` first."
            )

        for user_def in USERS:
            user, created = create_user(db, user_def)
            status = "created" if created else "exists (skipped)"
            print(
                f"  {user_def['role']:13s}  "
                f"{user_def['username']:18s}  "
                f"badge={user_def['badge_number']:8s}  "
                f"{status}"
            )
            if created:
                print(f"     password: {user_def['password']}")

    print("\n✓ Done. Use the credentials above to log in.\n")


if __name__ == "__main__":
    seed_users()