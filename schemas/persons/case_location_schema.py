from typing import List, Optional
from pydantic import BaseModel, ConfigDict

class LatLng(BaseModel):
    lat: float
    lng: float

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

class SceneSecurity(BaseModel):
    """The 'Scene Security' card content. Most of these aren't in the
    Location model today, so the service derives what it can from
    timeline/audit and leaves the rest as None — the UI already
    tolerates 'Pending' / italic placeholders."""
    securedBy:   Optional[str] = None
    securedOn:   Optional[str] = None      # "15 Dec 2025, 11:30 PM"
    releasedOn:  Optional[str] = None      # "Pending" when not yet released

class ProximityInfo(BaseModel):
    nearestPoliceStation: Optional[str] = None   # "Clifton Police Station – 1.2 km"
    nearestHospital:      Optional[str] = None
    landmarks:            List[str] = []

class NearbyCase(BaseModel):
    """One pin on the map AND one row in the 'Nearby Cases' list. The
    JSX expects the same shape in both places."""
    model_config = ConfigDict(from_attributes=True)

    id:        str           # external case_id, e.g. "C-2040"
    title:     str
    crimeType: str           # "Robbery", "Theft", …
    date:      str           # "20 Oct 2026" — display-ready
    severity:  str           # "Critical" | "High" | "Medium" | "Low"

    lat:       Optional[float] = None
    lng:       Optional[float] = None

    distanceKm: float = 0.0


class CaseLocationResponse(BaseModel):
    case_id:       str            # echo of the URL param, for sanity
    has_location:  bool           # false → JSX should show empty state

    address:       Optional[CrimeSceneAddress] = None
    security:      SceneSecurity     = SceneSecurity()
    proximity:     ProximityInfo     = ProximityInfo()
    notes:         Optional[str]     = None

    nearby:        List[NearbyCase]  = []