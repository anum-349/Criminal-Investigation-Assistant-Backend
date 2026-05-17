from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from db import get_db
from dependencies.auth import get_current_user
from models import User
from services import notification_service as svc

router = APIRouter()
        

# backend: add a debug route (remove in production)
@router.post("/notify")
async def debug_notify(db: Session = Depends(get_db), user=Depends(get_current_user)):
    svc.push(
        db,
        user_id=user.id,
        type="CASE_UPDATE",
        title="WS test notification",
        message="If you see this, WS works end to end.",
        severity_label="Normal",
    )
    db.commit()
    return {"ok": True}

@router.get("")
def list_notifications(
    request:   Request,
    page:      int = Query(1,  ge=1),
    page_size: int = Query(20, ge=1, le=100),
    unread_only: bool = Query(False),
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    return svc.list_notifications(
        db, user=user, page=page,
        page_size=page_size, unread_only=unread_only,
        request=request,
    )


@router.patch("/{notification_id}/read")
def mark_read(
    notification_id: str,
    request: Request,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    return svc.mark_read(db, user=user,
                         notification_id=notification_id, request=request)


@router.patch("/read-all")
def mark_all_read(
    request: Request,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    return svc.mark_all_read(db, user=user, request=request)


@router.delete("/{notification_id}")
def delete_notification(
    notification_id: str,
    request: Request,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    return svc.delete_notification(db, user=user,
                                   notification_id=notification_id,
                                   request=request)


@router.get("/preferences")
def get_preferences(
    request: Request,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    print("here")
    return svc.get_preferences(db, user=user)


@router.patch("/preferences")
def update_preferences(
    body:    dict,
    request: Request,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    return svc.update_preferences(db, user=user, prefs=body)

