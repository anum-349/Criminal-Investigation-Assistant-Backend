from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from db import get_db
from dependencies.auth import get_current_user
from models import User
from schemas.admin.analytics_schema import (
    AnalyticsOverviewResponse, AnalyticsTrendsResponse,
    AnalyticsBreakdownResponse, AnalyticsHeatmapResponse,
    AnalyticsPredictionsResponse,
)
from services.admin import analytics_service as svc
from schemas.leads.predictive_ai_schema import PredictiveAnalyticsResponse

router = APIRouter()

# Shared filter params
def _filters(
    date_range: str = Query("Last 9 months"),
    province:   str = Query("All Provinces"),
    crime_type: str = Query("All Types"),
):
    return date_range, province, crime_type


@router.get("/overview", response_model=AnalyticsOverviewResponse)
def overview(
    request:    Request,
    filters     = Depends(_filters),
    db: Session = Depends(get_db),
    user: User  = Depends(get_current_user),
):
    d, p, c = filters
    return svc.get_overview(db, date_range=d, province=p,
                            crime_type=c, user=user, request=request)


@router.get("/trends", response_model=AnalyticsTrendsResponse)
def trends(
    request:    Request,
    filters     = Depends(_filters),
    db: Session = Depends(get_db),
    user: User  = Depends(get_current_user),
):
    d, p, c = filters
    return svc.get_trends(db, date_range=d, province=p,
                          crime_type=c, user=user, request=request)


@router.get("/breakdown", response_model=AnalyticsBreakdownResponse)
def breakdown(
    request:    Request,
    filters     = Depends(_filters),
    db: Session = Depends(get_db),
    user: User  = Depends(get_current_user),
):
    d, p, c = filters
    return svc.get_breakdown(db, date_range=d, province=p,
                             crime_type=c, user=user, request=request)


@router.get("/heatmap", response_model=AnalyticsHeatmapResponse)
def heatmap(
    request:    Request,
    date_range: str = Query("Last 9 months"),
    db: Session = Depends(get_db),
    user: User  = Depends(get_current_user),
):
    return svc.get_heatmap(db, date_range=date_range,
                           user=user, request=request)

@router.get(
    "/predictions",
    response_model=PredictiveAnalyticsResponse,
    summary="Predictive analytics — forecast, pattern alerts, hotspot clustering",
)
def get_predictions_route(
    db:      Session = Depends(get_db),
    user             = Depends(get_current_user),
    request: Request = None,
):
    """
    Runs the predictive AI pipeline:
        - Time-series forecasting (Holt-Winters + linear ensemble)
        - Anomaly detection on historical months
        - Pattern surge alerts (per-province, per-crime-type)
        - DBSCAN spatial clustering for hotspot prediction
 
    Returns the full payload including legacy fields for backwards
    compatibility with the existing Predictions tab in the frontend.
    """
    return svc.get_predictions(db, user=user, request=request)
