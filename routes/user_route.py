from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from db import get_db
from schemas.user_schema import (
    UserRegister, UserLogin, UserUpdate, InvestigatorUpdate, TokenResponse,
)
from services.user_service import (
    register_user, login_user,
    update_user_profile, update_investigator_profile,
)
from dependencies.auth import get_current_user
from models import User

router = APIRouter()


# ─── REGISTER ────────────────────────────────────────────────────────────────

@router.post("/register", response_model=TokenResponse)
def register(user: UserRegister, db: Session = Depends(get_db)):
    try:
        return register_user(
            db,
            username=user.username,
            password=user.password,
            role=user.role,
            secret_code=user.secret_code,
            email=user.email,
        )
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ─── LOGIN ───────────────────────────────────────────────────────────────────

@router.post("/login", response_model=TokenResponse)
def login(user: UserLogin, db: Session = Depends(get_db)):
    try:
        return login_user(
            db,
            identifier=user.identifier,
            password=user.password,
            secret_code=user.secret_code,
        )
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ─── /me — token validation + current user info ──────────────────────────────
# This is the endpoint the FRONTEND should call on page load to verify
# the stored token is still valid. If it returns 200, stay logged in.
# If 401, clear the token and redirect to login.
# This is the fix for "asks login again after every refresh" — the bug
# was server-side (decode used wrong key), but the frontend should also
# adopt /me as its session-check endpoint.

@router.get("/me")
def get_me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "username": current_user.username,
        "badge_number": current_user.badge_number,
        "email": current_user.email,
        "role": current_user.role,
        "status": current_user.status,
        "picture_url": current_user.picture_url,
        "last_login": current_user.last_login,
    }


# ─── PROFILE UPDATES ─────────────────────────────────────────────────────────

@router.put("/profile")
def complete_profile(
    data: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        user = update_user_profile(db, current_user.id, data.model_dump(exclude_unset=True))
        return {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "contact_info": user.contact_info,
            "address": user.address,
            "picture_url": user.picture_url,
        }
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.put("/investigator/profile")
def complete_investigator_profile(
    data: InvestigatorUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role != "investigator":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only investigators have an investigator profile.",
        )
    try:
        return update_investigator_profile(
            db, current_user.id, data.model_dump(exclude_unset=True)
        )
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))