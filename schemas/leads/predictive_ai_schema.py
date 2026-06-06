from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict

from schemas.admin.analytics_schema import (
    PredictionTrendItem, ProvinceForecastItem, CrimeForecastItem,
)


class PatternAlertItem(BaseModel):
    """One AI-generated pattern alert card.

    Field naming matches the static PATTERN_ALERTS dummy data in
    src/pages/investigator/analytics/page.jsx so the same renderer works
    end-to-end with no UI changes.
    """
    model_config = ConfigDict(from_attributes=True)

    id:          int
    severity:    str             # "critical" | "high" | "medium" | "low"
    title:       str
    description: str
    confidence:  int             # 0-100
    timeframe:   str             # human label e.g. "Next 2 weeks"
    crimeType:   str
    province:    str
    modelName:   Optional[str] = None    # which model produced this alert


class HotspotPredictionItem(BaseModel):
    """One predicted hotspot, ready to drop on a Leaflet/Mapbox marker."""
    model_config = ConfigDict(from_attributes=True)

    id:         str              # e.g. "HS-001"
    city:       str
    province:   str
    lat:        float
    lng:        float
    cases:      int              # # incidents in the cluster
    crimeType:  str              # most-common crime type at the cluster
    riskScore:  float            # higher = more dangerous
    confidence: int              # 0-100, derived from cluster tightness


class ModelMetaItem(BaseModel):
    """Metadata about the AI run — useful for the 'Predictions' info banner
    and for debugging when something looks off."""
    model_config = ConfigDict(from_attributes=True)

    primaryModel:             str          # "holt_winters" | "linear" | "naive"
    historyMonths:            int          # how many months of training data
    forecastMonths:           int          # horizon
    mape:                     float        # mean abs % error on holdout
    confidenceLevel:          int          # band coverage, e.g. 90
    trainedAt:                datetime
    provincesCovered:         int
    crimeTypesCovered:        int
    sampleSize:               int          # total cases used for training
    recentIncidentsClustered: int
    hotspotsFound:            int


class PredictiveAnalyticsResponse(BaseModel):
    """The full payload returned by GET /api/analytics/predictions.

    Backwards-compatible: the first three fields are identical to the old
    AnalyticsPredictionsResponse.
    """
    model_config = ConfigDict(from_attributes=True)

    predictionTrend:     List[PredictionTrendItem]
    provinceForecast:    List[ProvinceForecastItem]
    crimeForecast:       List[CrimeForecastItem]
    patternAlerts:       List[PatternAlertItem]       = []
    hotspotPredictions:  List[HotspotPredictionItem]  = []
    modelMeta:           Optional[ModelMetaItem]      = None
    shapExplanation:      Optional[dict] = None   
    limeExplanation:      Optional[dict] = None 
    evaluationMetrics:    Optional[dict] = None