from fastapi import APIRouter, Depends
from dependencies.auth import get_current_user

router = APIRouter()

@router.get("/investigator")
def investigator_dashboard(user=Depends(get_current_user)):
    return {"msg": f"Welcome Investigator {user.username}"}