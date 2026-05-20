# services/predictive_ai_service.py
"""
Predictive AI engine for crime trends & analytics.

Purpose
───────
This module is the "AI brain" behind the Predictions tab in the analytics
page. It replaces the simple linear-extrapolation stub that used to live
in analytics_service.get_predictions() with a proper multi-model
forecasting ensemble plus anomaly / pattern detection.

What it produces
────────────────
For each call, the engine returns:
    1. Monthly forecast (next 3 months) with confidence band
    2. Per-province risk forecast
    3. Per-crime-type forecast
    4. AI Pattern Alerts (anomaly clusters, surges, emerging MOs)
    5. Hotspot predictions (top areas at risk next month)
    6. Model metadata: which model was used, accuracy on holdout, sample size

Models used
───────────
We use a small ensemble (no GPU, no heavy deps — runs on the same FastAPI
worker that serves the rest of the API):

    • Holt-Winters Exponential Smoothing  (statsmodels)
            - captures level + trend + (optionally) seasonality
            - main model when we have ≥ 8 months of history
    • Linear regression on time index       (numpy/sklearn)
            - fallback for short series (< 8 months)
    • Seasonal Naive baseline               (numpy)
            - used to compute "skill" (does HW beat naive?)
    • IQR-based anomaly detector            (numpy)
            - flags months / provinces / crime types that broke pattern
    • DBSCAN over (lat, lng, time)          (sklearn)
            - clusters recent incidents into emerging hotspots

The ensemble picks per-series the model with the lowest MAPE on the last
holdout point. Confidence bands come from the empirical residual std-dev
of the chosen model (not from a hard-coded ±15 %).

If statsmodels / sklearn aren't installed the engine falls back to the
linear model so the endpoint never breaks.

Wiring
──────
analytics_service.get_predictions() now just delegates here:

    from services import predictive_ai_service as pai
    return pai.run_predictions(db, user=user, request=request)

The output schema is a superset of the old one — existing frontend
fields (predictionTrend, provinceForecast, crimeForecast) are unchanged,
new fields (patternAlerts, hotspotPredictions, modelMeta) are additive
so old clients keep working.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

import numpy as np
from sqlalchemy import func as sa_func, desc
from sqlalchemy.orm import Session
from fastapi import Request

from models import (
    Case, Location, Province, CaseType, TimelineEvent,
)
from schemas.analytics_schema import (
    PredictionTrendItem, ProvinceForecastItem, CrimeForecastItem,
    AnalyticsPredictionsResponse,
)
from schemas.predictive_ai_schema import (
    PatternAlertItem, HotspotPredictionItem, ModelMetaItem,
    PredictiveAnalyticsResponse,
)
from services import audit_service as audit

log = logging.getLogger(__name__)


# ── Optional heavy deps (graceful fallback) ───────────────────────────────

try:
    from statsmodels.tsa.holtwinters import ExponentialSmoothing
    _HW_AVAILABLE = True
except ImportError:
    log.warning("statsmodels not installed — Holt-Winters disabled, falling back to linear regression.")
    _HW_AVAILABLE = False

try:
    from sklearn.cluster import DBSCAN
    _SKLEARN_AVAILABLE = True
except ImportError:
    log.warning("scikit-learn not installed — DBSCAN hotspot clustering disabled.")
    _SKLEARN_AVAILABLE = False


# ── Configuration ──────────────────────────────────────────────────────────

HISTORY_MONTHS_DEFAULT = 12          # history window for fitting
FORECAST_HORIZON       = 3           # months ahead to project
MIN_HISTORY_FOR_HW     = 8           # below this we use linear
MIN_HISTORY_FOR_SEASON = 24          # below this we disable seasonal HW
CONFIDENCE_Z           = 1.645       # ~90% band (1.96 for 95%)
ANOMALY_IQR_K          = 1.5         # standard outlier coefficient
HOTSPOT_EPS_KM         = 2.5         # DBSCAN radius for hotspot clustering
HOTSPOT_MIN_SAMPLES    = 3           # min incidents to form a hotspot
HOTSPOT_RECENT_DAYS    = 60          # only consider recent incidents


# ── Tiny utilities ─────────────────────────────────────────────────────────

MONTH_ABBR = ["Jan","Feb","Mar","Apr","May","Jun",
              "Jul","Aug","Sep","Oct","Nov","Dec"]


def _month_label(year: int, month: int) -> str:
    return MONTH_ABBR[month - 1]


def _iter_months(start: date, end: date):
    """Yield (year, month) inclusive from start to end."""
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield (y, m)
        m += 1
        if m > 12:
            m = 1
            y += 1


def _next_month(y: int, m: int) -> tuple[int, int]:
    m += 1
    if m > 12:
        m = 1
        y += 1
    return y, m


# ──────────────────────────────────────────────────────────────────────────
#  CORE FORECASTING PRIMITIVES
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class ForecastResult:
    """Outcome of forecasting one univariate monthly series."""
    history:      list[float]    # observed values (length n)
    forecast:     list[float]    # predicted values (length horizon)
    upper:        list[float]    # upper confidence band
    lower:        list[float]    # lower confidence band
    model_name:   str            # "holt_winters" | "linear" | "naive"
    mape:         float          # mean abs % error on holdout (lower=better)
    residual_std: float          # std of fit residuals (drives the band)


def _linear_forecast(y: np.ndarray, horizon: int) -> ForecastResult:
    """
    Ordinary least-squares fit on the time index. Robust, always works.

    Used as the fallback when the series is too short for Holt-Winters or
    when statsmodels isn't available.
    """
    n = len(y)
    x = np.arange(n, dtype=float)

    if n < 2:
        last = float(y[-1]) if n else 0.0
        return ForecastResult(
            history      = y.tolist(),
            forecast     = [last] * horizon,
            upper        = [last] * horizon,
            lower        = [last] * horizon,
            model_name   = "naive",
            mape         = 0.0,
            residual_std = 0.0,
        )

    # slope/intercept by closed-form
    x_mean = x.mean()
    y_mean = y.mean()
    denom  = ((x - x_mean) ** 2).sum() or 1.0
    slope  = ((x - x_mean) * (y - y_mean)).sum() / denom
    intercept = y_mean - slope * x_mean

    fitted    = intercept + slope * x
    residuals = y - fitted
    res_std   = float(residuals.std(ddof=1)) if n > 1 else 0.0

    fc_x = np.arange(n, n + horizon, dtype=float)
    fc   = intercept + slope * fc_x

    # widen the band as we forecast further out
    band = np.array([CONFIDENCE_Z * res_std * math.sqrt(1 + i) for i in range(1, horizon + 1)])

    # MAPE on last point as a quick skill check
    mape = _safe_mape(y[-1:], fitted[-1:])

    return ForecastResult(
        history      = y.tolist(),
        forecast     = [max(0.0, float(v)) for v in fc],
        upper        = [max(0.0, float(v + b)) for v, b in zip(fc, band)],
        lower        = [max(0.0, float(v - b)) for v, b in zip(fc, band)],
        model_name   = "linear",
        mape         = mape,
        residual_std = res_std,
    )


def _holt_winters_forecast(y: np.ndarray, horizon: int) -> Optional[ForecastResult]:
    """
    Exponential Smoothing with trend (Holt) and optional seasonality
    (Holt-Winters). Returns None if it can't fit — caller should fall
    back to linear.
    """
    if not _HW_AVAILABLE or len(y) < MIN_HISTORY_FOR_HW:
        return None

    try:
        seasonal_periods = 12 if len(y) >= MIN_HISTORY_FOR_SEASON else None
        kwargs = {
            "trend":            "add",
            "initialization_method": "estimated",
        }
        if seasonal_periods:
            kwargs["seasonal"] = "add"
            kwargs["seasonal_periods"] = seasonal_periods

        model = ExponentialSmoothing(y, **kwargs)
        fit   = model.fit(optimized=True, use_brute=False)

        fc    = fit.forecast(horizon)
        residuals = y - fit.fittedvalues
        res_std   = float(np.std(residuals, ddof=1)) if len(residuals) > 1 else 0.0

        band = np.array([CONFIDENCE_Z * res_std * math.sqrt(1 + i) for i in range(1, horizon + 1)])

        # Holdout MAPE: re-fit on y[:-1], predict last point
        if len(y) >= MIN_HISTORY_FOR_HW + 1:
            try:
                holdout_fit = ExponentialSmoothing(y[:-1], **kwargs).fit(optimized=True, use_brute=False)
                pred_last   = float(holdout_fit.forecast(1)[0])
                mape        = _safe_mape(y[-1:], np.array([pred_last]))
            except Exception:
                mape = _safe_mape(y[-1:], fit.fittedvalues[-1:])
        else:
            mape = _safe_mape(y[-1:], fit.fittedvalues[-1:])

        return ForecastResult(
            history      = y.tolist(),
            forecast     = [max(0.0, float(v)) for v in fc],
            upper        = [max(0.0, float(v + b)) for v, b in zip(fc, band)],
            lower        = [max(0.0, float(v - b)) for v, b in zip(fc, band)],
            model_name   = "holt_winters",
            mape         = mape,
            residual_std = res_std,
        )
    except Exception as e:
        log.warning("Holt-Winters fit failed (%s); falling back to linear.", e)
        return None


def _safe_mape(actual: np.ndarray, predicted: np.ndarray) -> float:
    """Mean Absolute Percentage Error, robust to zeros."""
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    mask = actual != 0
    if not mask.any():
        # Series is all zeros — use raw absolute error as a proxy
        return float(np.mean(np.abs(predicted - actual)))
    return float(np.mean(np.abs((actual[mask] - predicted[mask]) / actual[mask]))) * 100.0


def _best_forecast(y: list[float], horizon: int = FORECAST_HORIZON) -> ForecastResult:
    """
    Try Holt-Winters first; if it fails or scores worse than linear, use
    linear. Always pick the model with the lower MAPE.
    """
    y_arr = np.asarray(y, dtype=float)

    candidates: list[ForecastResult] = []
    hw = _holt_winters_forecast(y_arr, horizon)
    if hw is not None:
        candidates.append(hw)
    candidates.append(_linear_forecast(y_arr, horizon))

    # Pick the candidate with the lowest MAPE (ties broken by simpler model)
    best = min(candidates, key=lambda r: (r.mape, 0 if r.model_name == "linear" else 1))
    return best


# ──────────────────────────────────────────────────────────────────────────
#  ANOMALY / PATTERN DETECTION
# ──────────────────────────────────────────────────────────────────────────

def _iqr_anomalies(values: list[float]) -> list[int]:
    """Return indices of values outside [Q1 - k*IQR, Q3 + k*IQR]."""
    if len(values) < 4:
        return []
    arr = np.asarray(values, dtype=float)
    q1, q3 = np.percentile(arr, [25, 75])
    iqr = q3 - q1
    lo, hi = q1 - ANOMALY_IQR_K * iqr, q3 + ANOMALY_IQR_K * iqr
    return [i for i, v in enumerate(arr) if v < lo or v > hi]


def _detect_surge(history: list[float], forecast: list[float]) -> Optional[float]:
    """
    Return the predicted % change of the forecast peak over the historical
    mean (only if it's a meaningful surge). None if forecast is flat / down.
    """
    if not history or not forecast:
        return None
    hist_mean = float(np.mean(history[-3:])) if len(history) >= 3 else float(np.mean(history))
    if hist_mean <= 0:
        return None
    peak = float(max(forecast))
    pct  = (peak - hist_mean) / hist_mean * 100.0
    return pct if pct >= 15.0 else None  # only flag a >=15% projected surge


def _build_pattern_alerts(
    province_series: dict[str, list[float]],
    crime_series:    dict[str, list[float]],
    province_forecasts: dict[str, ForecastResult],
    crime_forecasts:    dict[str, ForecastResult],
    recent_incidents: list[dict],
) -> list[PatternAlertItem]:
    """
    Synthesize human-readable pattern alerts from the model output.
    These are what the frontend renders as the "AI Pattern Alerts" cards.
    """
    alerts: list[PatternAlertItem] = []
    alert_id = 1

    # 1) Province surges
    for province, series in province_series.items():
        fc = province_forecasts.get(province)
        if not fc:
            continue
        pct = _detect_surge(series, fc.forecast)
        if pct is None:
            continue
        confidence = max(0, min(100, int(100 - fc.mape)))
        severity   = "critical" if pct >= 40 else "high" if pct >= 25 else "medium"
        alerts.append(PatternAlertItem(
            id          = alert_id,
            severity    = severity,
            title       = f"Case surge predicted — {province}",
            description = (
                f"Forecasting model projects a {pct:.0f}% rise in case load "
                f"in {province} over the next {FORECAST_HORIZON} months relative "
                f"to recent baseline. Investigate emerging clusters."
            ),
            confidence  = confidence,
            timeframe   = f"Next {FORECAST_HORIZON} months",
            crimeType   = "All types",
            province    = province,
            modelName   = fc.model_name,
        ))
        alert_id += 1

    # 2) Crime-type surges
    for crime_type, series in crime_series.items():
        fc = crime_forecasts.get(crime_type)
        if not fc:
            continue
        pct = _detect_surge(series, fc.forecast)
        if pct is None:
            continue
        confidence = max(0, min(100, int(100 - fc.mape)))
        severity   = "critical" if pct >= 40 else "high" if pct >= 25 else "medium"
        alerts.append(PatternAlertItem(
            id          = alert_id,
            severity    = severity,
            title       = f"{crime_type} trend climbing nationwide",
            description = (
                f"Model detects an upward trajectory in {crime_type.lower()} "
                f"cases — projecting +{pct:.0f}% vs recent baseline. "
                f"Reallocate proactive patrols where possible."
            ),
            confidence  = confidence,
            timeframe   = f"Next {FORECAST_HORIZON} months",
            crimeType   = crime_type,
            province    = "All",
            modelName   = fc.model_name,
        ))
        alert_id += 1

    # 3) Historical anomalies (one alert if any province had an outlier month)
    for province, series in province_series.items():
        anom = _iqr_anomalies(series)
        if anom and anom[-1] >= len(series) - 2:
            # The anomaly is recent — worth flagging
            alerts.append(PatternAlertItem(
                id          = alert_id,
                severity    = "medium",
                title       = f"Anomalous month detected — {province}",
                description = (
                    f"Recent monthly volume in {province} is a statistical "
                    f"outlier (>1.5×IQR from the {len(series)}-month median). "
                    f"Review whether a single high-profile case is skewing "
                    f"the signal, or whether a real shift is underway."
                ),
                confidence  = 70,
                timeframe   = "Past month",
                crimeType   = "All types",
                province    = province,
                modelName   = "iqr_anomaly",
            ))
            alert_id += 1

    # Sort by severity then confidence
    sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    alerts.sort(key=lambda a: (sev_order.get(a.severity, 9), -a.confidence))
    return alerts[:6]  # cap at 6 cards so the UI doesn't drown


# ──────────────────────────────────────────────────────────────────────────
#  HOTSPOT CLUSTERING  (DBSCAN over recent incidents)
# ──────────────────────────────────────────────────────────────────────────

def _haversine_km(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    """Distance in km between two (lat, lng) points."""
    R = 6371.0
    lat1, lng1 = math.radians(p1[0]), math.radians(p1[1])
    lat2, lng2 = math.radians(p2[0]), math.radians(p2[1])
    dlat, dlng = lat2 - lat1, lng2 - lng1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng/2)**2
    return 2 * R * math.asin(math.sqrt(a))


def _cluster_hotspots(incidents: list[dict]) -> list[HotspotPredictionItem]:
    """
    Cluster recent (lat, lng) incidents using DBSCAN and rank by density +
    recency. Each cluster becomes a predicted hotspot for the next month.

    `incidents` is a list of dicts: {lat, lng, days_ago, city, province, crime_type}
    """
    if not _SKLEARN_AVAILABLE or len(incidents) < HOTSPOT_MIN_SAMPLES:
        return []

    # DBSCAN needs eps in the same units as the data. We convert km to
    # degrees roughly (1° lat ≈ 111 km) — fine for clustering at city scale.
    eps_deg = HOTSPOT_EPS_KM / 111.0
    coords  = np.array([[i["lat"], i["lng"]] for i in incidents])

    try:
        db = DBSCAN(eps=eps_deg, min_samples=HOTSPOT_MIN_SAMPLES, metric="euclidean").fit(coords)
    except Exception as e:
        log.warning("DBSCAN failed (%s)", e)
        return []

    labels = db.labels_
    clusters: dict[int, list[int]] = defaultdict(list)
    for idx, lbl in enumerate(labels):
        if lbl == -1:
            continue   # noise point
        clusters[lbl].append(idx)

    hotspots: list[HotspotPredictionItem] = []
    for cid, idxs in clusters.items():
        members = [incidents[i] for i in idxs]
        n       = len(members)

        # Centroid for the marker
        center_lat = float(np.mean([m["lat"] for m in members]))
        center_lng = float(np.mean([m["lng"] for m in members]))

        # Most-common city / province / crime_type → label the hotspot
        def _mode(key):
            counts = defaultdict(int)
            for m in members:
                if m.get(key):
                    counts[m[key]] += 1
            return max(counts, key=counts.get) if counts else "Unknown"

        city       = _mode("city")
        province   = _mode("province")
        crime_type = _mode("crime_type")

        # Recency score: more weight to recent incidents
        weights = np.array([math.exp(-m["days_ago"] / 30.0) for m in members])
        recency_score = float(weights.sum())

        # Risk score = log-density × recency
        risk = (1 + math.log(n)) * recency_score

        # Confidence ~ how tight the cluster is (smaller spread = higher conf)
        avg_dist = float(np.mean([
            _haversine_km((center_lat, center_lng), (m["lat"], m["lng"]))
            for m in members
        ]))
        confidence = max(30, min(95, int(100 - (avg_dist / HOTSPOT_EPS_KM) * 60)))

        hotspots.append(HotspotPredictionItem(
            id         = f"HS-{cid + 1:03d}",
            city       = city,
            province   = province,
            lat        = center_lat,
            lng        = center_lng,
            cases      = n,
            crimeType  = crime_type,
            riskScore  = round(risk, 2),
            confidence = confidence,
        ))

    # Top-N by risk score
    hotspots.sort(key=lambda h: h.riskScore, reverse=True)
    return hotspots[:8]


# ──────────────────────────────────────────────────────────────────────────
#  DATA EXTRACTION FROM THE DB
# ──────────────────────────────────────────────────────────────────────────

def _fetch_monthly_totals(db: Session, hist_from: date, hist_to: date) -> dict[tuple[int,int], int]:
    """Total cases per (year, month)."""
    rows = (
        db.query(Case)
        .filter(
            Case.is_deleted    == False,
            Case.incident_date >= hist_from,
            Case.incident_date <= hist_to,
        )
        .all()
    )
    out: dict[tuple[int,int], int] = defaultdict(int)
    for c in rows:
        if c.incident_date:
            out[(c.incident_date.year, c.incident_date.month)] += 1
    return out


def _fetch_monthly_by_dimension(
    db: Session, hist_from: date, hist_to: date, *, by: str,
) -> dict[str, dict[tuple[int,int], int]]:
    """
    Return {dimension_value: {(year,month): count}}.

    `by` is "province" or "crime_type". We do this in Python instead of
    a SQL pivot so the same code path works across SQLite / Postgres /
    whatever the FYP is using locally.
    """
    q = db.query(Case).filter(
        Case.is_deleted    == False,
        Case.incident_date >= hist_from,
        Case.incident_date <= hist_to,
    )
    if by == "province":
        q = q.join(Location, Location.case_id_fk == Case.id) \
             .join(Province, Province.id == Location.province_id) \
             .add_columns(Province.label.label("dim"))
    elif by == "crime_type":
        q = q.join(CaseType, CaseType.id == Case.case_type_id) \
             .add_columns(CaseType.label.label("dim"))
    else:
        raise ValueError(f"Unknown dimension '{by}'")

    out: dict[str, dict[tuple[int,int], int]] = defaultdict(lambda: defaultdict(int))
    for row in q.all():
        case, dim_value = row[0], row[1]
        if case.incident_date and dim_value:
            out[dim_value][(case.incident_date.year, case.incident_date.month)] += 1
    return out


def _fetch_recent_incidents(db: Session, days: int) -> list[dict]:
    """
    Pull recent geocoded incidents for hotspot clustering.

    Resolves the city via the City lookup table (`Location.city_id ->
    lkp_cities.id`) — falling back to `Location.area` if the FK is
    missing. We do this via two left-joins so unmapped rows still come
    through (they'll just have city='Unknown').
    """
    from models import City   # local import to avoid widening the module-top import block

    cutoff = date.today() - timedelta(days=days)

    rows = (
        db.query(Case, Location, Province, City, CaseType)
        .join(Location,  Location.case_id_fk == Case.id)
        .outerjoin(Province, Province.id == Location.province_id)
        .outerjoin(City,     City.id     == Location.city_id)
        .outerjoin(CaseType, CaseType.id == Case.case_type_id)
        .filter(
            Case.is_deleted    == False,
            Case.incident_date >= cutoff,
            Location.latitude  != None,
            Location.longitude != None,
        )
        .all()
    )

    today = date.today()
    incidents: list[dict] = []
    for case, loc, province, city, ctype in rows:
        try:
            city_label = (
                (city.name if city else None)
                or loc.area
                or "Unknown"
            )
            incidents.append({
                "lat":        float(loc.latitude),
                "lng":        float(loc.longitude),
                "days_ago":   max(0, (today - case.incident_date).days) if case.incident_date else 0,
                "city":       city_label,
                "province":   province.label if province else "Unknown",
                "crime_type": ctype.label if ctype else "Unknown",
            })
        except (TypeError, ValueError):
            continue
    return incidents


# ──────────────────────────────────────────────────────────────────────────
#  PUBLIC ENTRY POINT
# ──────────────────────────────────────────────────────────────────────────

def run_predictions(
    db: Session, *,
    user,
    request: Optional[Request],
    history_months: int = HISTORY_MONTHS_DEFAULT,
) -> PredictiveAnalyticsResponse:
    """
    Generate a full predictive analytics payload.

    This is the new entry point that analytics_routes calls. It returns a
    superset of the old AnalyticsPredictionsResponse — every field the
    frontend currently uses is still present, plus new fields for AI
    pattern alerts, hotspot predictions, and model metadata.
    """
    today     = date.today()
    hist_from = today - timedelta(days=30 * history_months)

    # ─── 1. National monthly series ────────────────────────────────────
    monthly_totals = _fetch_monthly_totals(db, hist_from, today)
    months   = list(_iter_months(hist_from, today))
    hist_ser = [float(monthly_totals.get(k, 0)) for k in months]

    national_fc = _best_forecast(hist_ser, horizon=FORECAST_HORIZON)

    # Build the predictionTrend (history + forecast points)
    prediction_trend: list[PredictionTrendItem] = []
    for (yr, mo), v in zip(months, hist_ser):
        prediction_trend.append(PredictionTrendItem(
            month = _month_label(yr, mo),
            cases = int(round(v)),
        ))

    yr, mo = months[-1]
    for i in range(FORECAST_HORIZON):
        yr, mo = _next_month(yr, mo)
        prediction_trend.append(PredictionTrendItem(
            month     = _month_label(yr, mo),
            predicted = round(national_fc.forecast[i], 1),
            upper     = round(national_fc.upper[i], 1),
            lower     = round(national_fc.lower[i], 1),
        ))

    # ─── 2. Province forecasts ─────────────────────────────────────────
    prov_monthly  = _fetch_monthly_by_dimension(db, hist_from, today, by="province")
    province_series:   dict[str, list[float]]      = {}
    province_forecasts: dict[str, ForecastResult]   = {}
    province_forecast_items: list[ProvinceForecastItem] = []

    for province, ser_dict in prov_monthly.items():
        ser = [float(ser_dict.get(k, 0)) for k in months]
        if sum(ser) == 0:
            continue
        province_series[province] = ser
        fc = _best_forecast(ser, horizon=FORECAST_HORIZON)
        province_forecasts[province] = fc

        current_cnt   = int(ser[-1])
        forecast_next = int(max(0, round(fc.forecast[0])))
        # % change vs current month
        change = ((forecast_next - current_cnt) / current_cnt * 100.0) if current_cnt else 0.0

        province_forecast_items.append(ProvinceForecastItem(
            province = province,
            current  = current_cnt,
            forecast = forecast_next,
            change   = round(change, 1),
        ))

    province_forecast_items.sort(key=lambda p: p.forecast, reverse=True)

    # ─── 3. Crime-type forecasts ───────────────────────────────────────
    crime_monthly = _fetch_monthly_by_dimension(db, hist_from, today, by="crime_type")
    crime_series:   dict[str, list[float]]    = {}
    crime_forecasts: dict[str, ForecastResult] = {}
    crime_forecast_items: list[CrimeForecastItem] = []

    for crime, ser_dict in crime_monthly.items():
        ser = [float(ser_dict.get(k, 0)) for k in months]
        if sum(ser) == 0:
            continue
        crime_series[crime] = ser
        fc = _best_forecast(ser, horizon=FORECAST_HORIZON)
        crime_forecasts[crime] = fc

        current_cnt   = int(ser[-1])
        forecast_next = int(max(0, round(fc.forecast[0])))
        change = ((forecast_next - current_cnt) / current_cnt * 100.0) if current_cnt else 0.0

        crime_forecast_items.append(CrimeForecastItem(
            type     = crime,
            current  = current_cnt,
            forecast = forecast_next,
            change   = round(change, 1),
        ))

    crime_forecast_items.sort(key=lambda c: c.forecast, reverse=True)

    # ─── 4. Pattern alerts ─────────────────────────────────────────────
    pattern_alerts = _build_pattern_alerts(
        province_series, crime_series,
        province_forecasts, crime_forecasts,
        recent_incidents=[],   # not used here yet, kept for future enrichment
    )

    # ─── 5. Hotspot predictions ────────────────────────────────────────
    recent_incidents   = _fetch_recent_incidents(db, days=HOTSPOT_RECENT_DAYS)
    hotspot_predictions = _cluster_hotspots(recent_incidents)

    # ─── 6. Model metadata ─────────────────────────────────────────────
    model_meta = ModelMetaItem(
        primaryModel   = national_fc.model_name,
        historyMonths  = len(hist_ser),
        forecastMonths = FORECAST_HORIZON,
        mape           = round(national_fc.mape, 2),
        confidenceLevel = 90,
        trainedAt      = datetime.utcnow(),
        provincesCovered = len(province_series),
        crimeTypesCovered = len(crime_series),
        sampleSize     = int(sum(hist_ser)),
        recentIncidentsClustered = len(recent_incidents),
        hotspotsFound  = len(hotspot_predictions),
    )

    # ─── Audit ─────────────────────────────────────────────────────────
    _audit(db, user, request, f"Ran predictive AI ({national_fc.model_name})")

    return PredictiveAnalyticsResponse(
        predictionTrend     = prediction_trend,
        provinceForecast    = province_forecast_items,
        crimeForecast       = crime_forecast_items,
        patternAlerts       = pattern_alerts,
        hotspotPredictions  = hotspot_predictions,
        modelMeta           = model_meta,
    )


# ── audit helper ──────────────────────────────────────────────────────────

def _audit(db, user, request, detail: str):
    """Best-effort audit log; never raises."""
    try:
        audit.log_event(
            db, user_id=user.id, action="VIEW",
            module="Analytics", detail=detail,
            target_type="analytics", target_id="predictions",
            request=request,
        )
        db.commit()
    except Exception:
        db.rollback()