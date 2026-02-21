from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from schemas.user import InvestigatorUpdate, UserRegister, UserLogin, UserUpdate
from services.user_service import register_user, login_user, update_investigator_profile, update_user_profile
from auth.jwt import get_current_user

router = APIRouter()

@router.post("/register")
def register(user: UserRegister, db: Session = Depends(get_db)):
    try:
        return register_user(db, user.username, user.password, user.role, user.secret_code or None)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/login")
def login(user: UserLogin, db: Session = Depends(get_db)):
    try:
        return login_user(db, user.badge_number, user.password, user.secret_code or None)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    
@router.put("/profile")
def complete_profile(data: UserUpdate, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    return update_user_profile(db, current_user.id, data.model_dump(exclude_unset=True))

@router.put("/investigator/profile")
def complete_investigator_profile(data: InvestigatorUpdate, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    update_user_profile(db, current_user.id, data.model_dump(exclude={"department","rank","shift","specialization"}))
    return update_investigator_profile(db, current_user.id, data.model_dump(exclude_unset=True))