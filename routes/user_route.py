from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from db import get_db
from schemas.user_schema import (
    UserRegister, UserLogin, UserUpdate, InvestigatorUpdate, PasswordChange)
from schemas.response_schema import (
    TokenResponse, MeResponse, UserResponse, InvestigatorProfileResponse,
    MessageResponse,
)

from dependencies.auth import get_current_user
from models import User
from services.user_service import change_password, login_user, logout_user, register_user, update_investigator_profile, update_user_profile

router = APIRouter()

@router.post("/register", response_model=TokenResponse)
def register(
    user: UserRegister,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        return register_user(
            db,
            username=user.username,
            password=user.password,
            role=user.role,
            secret_code=user.secret_code,
            email=user.email,
            request=request,
        )
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

@router.post("/login", response_model=TokenResponse)
def login(
    user: UserLogin,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        return login_user(
            db,
            identifier=user.identifier,
            password=user.password,
            secret_code=user.secret_code,
            request=request,
        )
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

@router.post("/logout", response_model=MessageResponse)
def logout(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Writes the LOGOUT audit row. The frontend should call this BEFORE
    clearing its stored token.

    JWTs are stateless — we can't actually invalidate the token server-side
    without a token blocklist. Frontend is still responsible for dropping
    the token from local/sessionStorage.
    """
    return logout_user(db, current_user, request=request)

@router.get("/me", response_model=MeResponse)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user 

@router.put("/profile", response_model=UserResponse)
def complete_profile(
    data: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return update_user_profile(db, current_user.id, data.model_dump(exclude_unset=True))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

@router.put("/investigator/profile", response_model=InvestigatorProfileResponse)
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

@router.post("/change-password", response_model=MessageResponse)
def change_my_password(
    data: PasswordChange,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return change_password(
            db,
            current_user,
            data.current_password,
            data.new_password,
            request=request,
        )
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))