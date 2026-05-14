
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from db import get_db
from dependencies.auth import get_current_user
from models import Investigator, User


router = APIRouter()
 
 
@router.get(
    "/investigators",
    summary="List investigators for dropdowns (Assigned To, etc.).",
)
def list_investigators_endpoint(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    search:      Optional[str] = Query(default=None, description="Free-text filter (name / username / department)"),
    active_only: bool          = Query(default=True, description="If true, exclude deactivated accounts"),
):
    """Returns every investigator account — both admins and investigators
    can call this, since investigators sometimes need to see "who else
    is on this case?" while filling the form.
 
    Response shape (matches what investigatorApi.js expects):
        { "items": [
            { "id": int, "username": str, "fullName": str | None,
              "rank": str | None, "department": str | None,
              "displayLabel": str },
            …
          ] }
 
    `displayLabel` is the pretty string the frontend shows in the dropdown
    ("Insp. Rashid Ali Khan · Cybercrime"), built here so every consumer
    formats consistently.
    """
    q = (
        db.query(User)
        .outerjoin(Investigator, Investigator.id == User.id)
        .options(joinedload(User.investigator))
        .filter(User.role == "investigator")
    )
 
    # If your User model has a `status` column ("active" / "inactive"),
    # honour the active_only flag. If not, hasattr falls through.
    if active_only and hasattr(User, "status"):
        q = q.filter(User.status == "active")
 
    if search:
        s = f"%{search.strip()}%"
        # Investigator.department lives on the joined table; OR all the
        # plausible match columns and let the DB pick.
        q = q.filter(or_(
            User.username.ilike(s),
            getattr(User, "full_name", User.username).ilike(s),
            Investigator.department.ilike(s),
            Investigator.rank.ilike(s),
        ))
 
    rows = q.order_by(User.username.asc()).all()
 
    items = []
    for u in rows:
        inv = u.investigator
        rank = (inv.rank or "").strip() if inv else ""
        dept = (inv.department or "").strip() if inv else ""
        full_name = getattr(u, "full_name", None) or u.username
 
        # "Insp. Rashid Ali Khan · Cybercrime"
        label_parts = []
        if rank: label_parts.append(f"{rank}.")
        label_parts.append(full_name)
        display_label = " ".join(label_parts)
        if dept:
            display_label = f"{display_label} · {dept}"
 
        items.append({
            "id":           u.id,
            "username":     u.username,
            "fullName":     full_name,
            "rank":         rank or None,
            "department":   dept or None,
            "displayLabel": display_label,
        })
 
    return {"items": items}

