from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from db import get_db
from schemas.common.user_schema import (
    PictureUploadRequest, PreferencesPayload, PreferencesResponse, UserRegister, UserLogin, UserUpdate, InvestigatorUpdate, PasswordChange)
from schemas.common.response_schema import (
    TokenResponse, MeResponse, UserResponse, InvestigatorProfileResponse,
    MessageResponse,
)

from dependencies.auth import get_current_user
from models import Admin, User
from services.common.user_service import change_password, fetch_investigator_profile, get_preferences, login_user, logout_user, register_user, save_preferences, update_investigator_profile, update_user_profile, upload_profile_picture

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

@router.get("/investigator/profile", response_model=InvestigatorProfileResponse)
def get_investigator_profile(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role != "investigator":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only investigators have an investigator profile.",
        )
    try:
        return fetch_investigator_profile(db, current_user.id)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    
@router.put("/investigator/profile/picture")
def upload_picture(
    body: PictureUploadRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    picture_url = upload_profile_picture(db, current_user.id, body.data_url)
    return {"picture_url": picture_url}

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
    
@router.get("/preferences", response_model=PreferencesResponse)
def get_prefs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return {"preferences": get_preferences(db, current_user.id)}


@router.put("/preferences", response_model=PreferencesResponse)
def save_prefs(
    body: PreferencesPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return {"preferences": save_preferences(db, current_user.id, body.preferences)}

@router.get("/admin/profile")
def get_admin_profile(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Admins only.")
    
    admin_row = db.query(Admin).filter(Admin.id == current_user.id).first()
    return {
        "id":          current_user.id,
        "username":    current_user.username,
        "email":       current_user.email,
        "role":        current_user.role,
        "picture_url": current_user.picture_url,
        "status":      current_user.status,
        "admin_level": admin_row.admin_level if admin_row else None,
        "created_at":  current_user.created_at,
    }