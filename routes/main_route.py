from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta

from db import get_db
from dependencies.auth import (
    get_current_admin, get_current_investigator,
)
from models import User

router = APIRouter()

@router.get("/investigator")
def investigator_dashboard(
    user: User = Depends(get_current_investigator),
    db: Session = Depends(get_db),
):
    """Compact dashboard payload — counts the investigator can act on."""
    return {
        "msg":          f"Welcome, {user.username}",
        "user": {
            "id":           user.id,
            "username":     user.username,
            "badge_number": user.badge_number,
            "role":         user.role,
        },
        "stats": {
            "active_cases":       0,
            "pending_leads":      0,
            "unread_notifications": 0,
            "this_week_updates":  0,
        },
    }

@router.get("/admin")
def admin_dashboard(
    user: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Admin-only dashboard. Aggregate stats + system health."""
    total_users      = db.query(func.count(User.id)).scalar()
    active_users     = db.query(func.count(User.id)).filter(User.status == "active").scalar()
    recent_logins    = (
        db.query(func.count(User.id))
          .filter(User.last_login >= datetime.utcnow() - timedelta(hours=24))
          .scalar()
    )

    return {
        "msg": f"Welcome, Admin {user.username}",
        "user": {
            "id":       user.id,
            "username": user.username,
            "role":     user.role,
        },
        "stats": {
            "total_users":     total_users,
            "active_users":    active_users,
            "recent_logins":   recent_logins,
            "total_cases":     0,    # filled when Case CRUD lands
            "open_cases":      0,
            "ai_leads_today":  0,
        },
    }