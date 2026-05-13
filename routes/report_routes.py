from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from db import get_db
from dependencies.auth import get_current_user
from models import User
from schemas.report_schema import (
    GenerateReportRequest, ReportData, ReportHistoryList,
)
from services import report_service as svc

router = APIRouter()


@router.post("/generate", response_model=ReportData)
def generate_report(
    body:    GenerateReportRequest,
    request: Request,
    db:      Session = Depends(get_db),
    user:    User    = Depends(get_current_user),
):
    return svc.generate_report(db, user=user, body=body, request=request)


@router.get("/history", response_model=ReportHistoryList)
def get_history(
    request:   Request,
    page:      int = Query(1,  ge=1),
    page_size: int = Query(10, ge=1, le=100),
    db:        Session = Depends(get_db),
    user:      User    = Depends(get_current_user),
):
    return svc.get_report_history(db, user=user, page=page, page_size=page_size, request=request)