from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime


# ── Stats ──────────────────────────────────────────────────────────
class DashboardStats(BaseModel):
    new_cases_this_month: int
    total_active_cases: int
    reports_with_missing_data: int
    leads_found: int
    solved_cases: int


# ── Cases ──────────────────────────────────────────────────────────
class CaseListItem(BaseModel):
    id: str               # external case_id, e.g. "CASE-102"
    crime_type: str
    location: str
    status: str
    last_update: str      # ISO date string, formatted client-side

    class Config:
        from_attributes = True


# ── Activities ─────────────────────────────────────────────────────
class ActivityItem(BaseModel):
    id: int
    title: str
    case_id: Optional[str] = None      # external case_id label
    description: Optional[str] = None
    type: str
    created_at: datetime

    class Config:
        from_attributes = True


# ── Hotspots ───────────────────────────────────────────────────────
class HotspotItem(BaseModel):
    id: int
    city: str
    province: Optional[str] = None
    lat: float
    lng: float
    severity: str
    cases: int


# ── Combined dashboard payload ─────────────────────────────────────
class DashboardResponse(BaseModel):
    stats: DashboardStats
    active_cases: List[CaseListItem]
    activities: List[ActivityItem]
    hotspots: List[HotspotItem]