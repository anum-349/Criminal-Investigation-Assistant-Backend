from fastapi import APIRouter, Depends
from dependencies.auth import get_current_admin

router = APIRouter()

@router.get("/admin/dashboard")
def admin_dashboard(admin=Depends(get_current_admin)):
    return {"msg": f"Welcome Admin {admin.username}"}