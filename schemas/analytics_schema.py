from pydantic import BaseModel, ConfigDict
from typing import Optional, List


class MonthlyTrendItem(BaseModel):
    month:  str
    cases:  int
    solved: int
    leads:  int


class CrimeByTypeItem(BaseModel):
    type:  str
    count: int
    prev:  int


class CrimeByProvinceItem(BaseModel):
    name:  str
    value: int


class StatusDistItem(BaseModel):
    name:  str
    value: int


class SeverityTrendItem(BaseModel):
    month:    str
    critical: int
    high:     int
    medium:   int
    low:      int


class SummaryStats(BaseModel):
    totalCases:       int
    solvedThisMonth:  int
    avgResolutionDays: float
    highSeverity:     int
    totalCasesChange:      str
    solvedChange:          str
    avgResolutionChange:   str
    highSeverityChange:    str


class HeatmapData(BaseModel):
    days:  List[str]
    hours: List[str]
    data:  List[List[int]]   # [day][hour] = count


class PredictionTrendItem(BaseModel):
    month:     str
    cases:     Optional[int]   = None
    predicted: Optional[float] = None
    upper:     Optional[float] = None
    lower:     Optional[float] = None


class ProvinceForecastItem(BaseModel):
    province: str
    current:  int
    forecast: int
    change:   float


class CrimeForecastItem(BaseModel):
    type:     str
    current:  int
    forecast: int
    change:   float


class AnalyticsOverviewResponse(BaseModel):
    summary:         SummaryStats
    monthlyTrend:    List[MonthlyTrendItem]
    crimeByProvince: List[CrimeByProvinceItem]
    statusDist:      List[StatusDistItem]


class AnalyticsTrendsResponse(BaseModel):
    severityTrend: List[SeverityTrendItem]
    monthlyTrend:  List[MonthlyTrendItem]


class AnalyticsBreakdownResponse(BaseModel):
    crimeByType: List[CrimeByTypeItem]


class AnalyticsHeatmapResponse(BaseModel):
    heatmap: HeatmapData


class AnalyticsPredictionsResponse(BaseModel):
    predictionTrend:  List[PredictionTrendItem]
    provinceForecast: List[ProvinceForecastItem]
    crimeForecast:    List[CrimeForecastItem]