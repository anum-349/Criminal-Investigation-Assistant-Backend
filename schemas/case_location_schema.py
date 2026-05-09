"""
schemas/case_location_schema.py
─────────────────────────────────────────────────────────────────────────────
Response shapes for the case-detail "Location" tab
(src/pages/investigator/case/[id]/CaseLocation.jsx).

Endpoint these power:
  GET /api/investigator/cases/{case_id}/location
        ?nearby_radius_km=3&nearby_limit=10

The JSX renders three blocks: a LeafletMap (single mode) with a primary
pin + optional nearby pins, a stack of detail cards (address, security,
proximity, notes), and a "Nearby Cases" list. Field names below are
chosen to match what the JSX reads directly so the component can render
without shape-massaging.
"""

from typing import List, Optional
from pydantic import BaseModel, ConfigDict


# ─── Coordinates ────────────────────────────────────────────────────────────

class LatLng(BaseModel):
    lat: float
    lng: float


# ─── Address card ───────────────────────────────────────────────────────────

class CrimeSceneAddress(BaseModel):
    """Top section of the detail panel."""
    model_config = ConfigDict(from_attributes=True)

    address:           str                     # "Flat 4B, Block 7, …"
    area:              Optional[str] = None    # "Clifton, Karachi"
    city:              Optional[str] = None
    province:          Optional[str] = None
    crimeSceneType:    Optional[str] = None    # "Residential – Indoor"
    accessStatus:      Optional[str] = None    # "Secured", "Open", …
    coordinates:       Optional[LatLng] = None # null when no lat/lng on file


# ─── Scene-security card ────────────────────────────────────────────────────

class SceneSecurity(BaseModel):
    """The 'Scene Security' card content. Most of these aren't in the
    Location model today, so the service derives what it can from
    timeline/audit and leaves the rest as None — the UI already
    tolerates 'Pending' / italic placeholders."""
    securedBy:   Optional[str] = None
    securedOn:   Optional[str] = None      # "15 Dec 2025, 11:30 PM"
    releasedOn:  Optional[str] = None      # "Pending" when not yet released


# ─── Proximity card ─────────────────────────────────────────────────────────

class ProximityInfo(BaseModel):
    nearestPoliceStation: Optional[str] = None   # "Clifton Police Station – 1.2 km"
    nearestHospital:      Optional[str] = None
    landmarks:            List[str] = []


# ─── A single nearby case ───────────────────────────────────────────────────

class NearbyCase(BaseModel):
    """One pin on the map AND one row in the 'Nearby Cases' list. The
    JSX expects the same shape in both places."""
    model_config = ConfigDict(from_attributes=True)

    id:        str           # external case_id, e.g. "C-2040"
    title:     str
    crimeType: str           # "Robbery", "Theft", …
    date:      str           # "20 Oct 2026" — display-ready
    severity:  str           # "Critical" | "High" | "Medium" | "Low"

    # Map fields — for the nearbyPins prop on LeafletMap
    lat:       Optional[float] = None
    lng:       Optional[float] = None

    # Distance — kilometres, rounded to 1 dp; rendered as "0.8 km" by frontend
    distanceKm: float = 0.0


# ─── Top-level response envelope ────────────────────────────────────────────

class CaseLocationResponse(BaseModel):
    case_id:       str            # echo of the URL param, for sanity
    has_location:  bool           # false → JSX should show empty state

    # Card data
    address:       Optional[CrimeSceneAddress] = None
    security:      SceneSecurity     = SceneSecurity()
    proximity:     ProximityInfo     = ProximityInfo()
    notes:         Optional[str]     = None

    # List + pins
    nearby:        List[NearbyCase]  = []