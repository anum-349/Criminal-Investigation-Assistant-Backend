import math
from typing import List, Optional, Tuple
from datetime import datetime

from sqlalchemy import and_, or_, desc, asc
from sqlalchemy.orm import Session, joinedload
from fastapi import Request, HTTPException

from models import (
    User,
    Case, 
    Location,
    TimelineEvent, TimelineEventType,
)
from services import audit_service as audit
from schemas.case_location_schema import (
    LatLng,
    CrimeSceneAddress,
    SceneSecurity,
    ProximityInfo,
    NearbyCase,
    CaseLocationResponse,
)
from services.service_helper import _resolve_case

EARTH_RADIUS_KM = 6371.0

DEFAULT_NEARBY_RADIUS_KM = 3.0
MAX_NEARBY_RESULTS       = 50

SCENE_SECURED_CODES  = ("SCENE_SECURED", "SCENE_SEALED", "SCENE_PROCESSED")
SCENE_RELEASED_CODES = ("SCENE_RELEASED",)

def _normalize_severity(label: Optional[str]) -> str:
    if not label:
        return "Low"
    s = label.strip().lower()
    if s.startswith("crit"):  return "Critical"
    if s == "high":           return "High"
    if s == "medium":         return "Medium"
    if s == "low":            return "Low"
    return "Low"

def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two (lat, lng) pairs in kilometres."""
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlng / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_KM * c


def _bbox(lat: float, lng: float, radius_km: float) -> Tuple[float, float, float, float]:
    """Return (lat_min, lat_max, lng_min, lng_max) for a degree-based
    bounding box that fully contains the given radius. Used as a cheap
    pre-filter before the haversine refinement."""
    # 1° of latitude ≈ 111.32 km everywhere.
    dlat = radius_km / 111.32
    # 1° of longitude shrinks with latitude (cos).
    cos_lat = max(0.000001, math.cos(math.radians(lat)))
    dlng = radius_km / (111.32 * cos_lat)
    return (lat - dlat, lat + dlat, lng - dlng, lng + dlng)


def _format_full_address(loc: Location) -> str:
    """Pick the most-detailed address string we can without duplicating."""
    return loc.full_address or loc.display_address or "—"


def _format_area_line(loc: Location) -> Optional[str]:
    """Combines area + city, e.g. 'Clifton, Karachi'."""
    parts = []
    if loc.area:
        parts.append(loc.area)
    if loc.city and loc.city.name:
        parts.append(loc.city.name)
    return ", ".join(parts) if parts else None


def _format_when(d: Optional[datetime]) -> Optional[str]:
    """Display-ready: '15 Dec 2025, 11:30 PM'."""
    if not d:
        return None
    return d.strftime("%d %b %Y, %I:%M %p").lstrip("0")


def _format_event_date(d) -> str:
    """For nearby-case rows: '20 Oct 2026'. Accepts datetime or date."""
    if not d:
        return "—"
    return d.strftime("%d %b %Y")


def _split_landmarks(raw: Optional[str]) -> List[str]:
    """Location.landmarks is a single Text column. Investigators can write
    one per line OR comma-separated. We accept both."""
    if not raw:
        return []
    bits = []
    for line in raw.splitlines():
        if "," in line:
            bits.extend(line.split(","))
        else:
            bits.append(line)
    return [b.strip() for b in bits if b.strip()]


def _derive_scene_security(db: Session, case: Case) -> SceneSecurity:
    """
    Build the SceneSecurity card content.

    Today the Location model has no explicit secured_by/secured_on/released_on
    columns, so we look for SCENE_SECURED / SCENE_RELEASED timeline events.
    If none exist:
      - secured_on falls back to the case's created_at (a reasonable proxy
        for "scene first secured" since you can't register a case without
        the scene being known)
      - released_on stays None → JSX shows "Pending"
    """
    secured_event = (
        db.query(TimelineEvent)
        .join(TimelineEventType, TimelineEvent.event_type_id == TimelineEventType.id)
        .filter(
            TimelineEvent.case_id_fk == case.id,
            TimelineEventType.code.in_(SCENE_SECURED_CODES),
        )
        .order_by(asc(TimelineEvent.created_at))
        .first()
    )
    released_event = (
        db.query(TimelineEvent)
        .join(TimelineEventType, TimelineEvent.event_type_id == TimelineEventType.id)
        .filter(
            TimelineEvent.case_id_fk == case.id,
            TimelineEventType.code.in_(SCENE_RELEASED_CODES),
        )
        .order_by(desc(TimelineEvent.created_at))
        .first()
    )

    secured_by = None
    secured_on = None
    if secured_event:
        secured_by = secured_event.officer_name
        secured_on = _format_when(secured_event.created_at)
    else:
        if case.assigned_to and case.assigned_to.user:
            inv = case.assigned_to
            rank = (inv.rank or "").strip()
            uname = inv.user.username
            secured_by = f"{rank}. {uname}" if rank else uname
        secured_on = _format_when(case.created_at)

    released_on = _format_when(released_event.created_at) if released_event else "Pending"

    return SceneSecurity(
        securedBy=secured_by,
        securedOn=secured_on,
        releasedOn=released_on,
    )


def _derive_proximity(loc: Location) -> ProximityInfo:
    """Best-effort proximity card from what the Location model gives us."""
    police_line = None
    if loc.police_station:
        police_line = loc.police_station

    return ProximityInfo(
        nearestPoliceStation=police_line,
        nearestHospital=None,                 # TODO: hospital dataset
        landmarks=_split_landmarks(loc.landmarks),
    )


def _find_nearby_cases(
    db: Session,
    *,
    parent_case: Case,
    center: LatLng,
    radius_km: float,
    limit: int,
) -> List[NearbyCase]:
    """Spatial query — see module docstring for the bbox-then-haversine plan."""
    lat_min, lat_max, lng_min, lng_max = _bbox(center.lat, center.lng, radius_km)

    q = (
        db.query(Case)
        .join(Location, Location.case_id_fk == Case.id)
        .filter(
            Case.is_deleted == False,                # noqa: E712
            Case.id != parent_case.id,
            Location.latitude.isnot(None),
            Location.longitude.isnot(None),
            Location.latitude.between(lat_min, lat_max),
            Location.longitude.between(lng_min, lng_max),
        )
        .options(
            joinedload(Case.case_type),
            joinedload(Case.priority),
            joinedload(Case.location),
        )
    )

    candidates = q.all()

    rows: List[NearbyCase] = []
    for c in candidates:
        loc = c.location
        if not loc or loc.latitude is None or loc.longitude is None:
            continue
        d = _haversine_km(center.lat, center.lng, loc.latitude, loc.longitude)
        if d > radius_km:
            continue
        rows.append(
            NearbyCase(
                id=c.case_id,
                title=c.case_title or "—",
                crimeType=c.case_type.label if c.case_type else "—",
                date=_format_event_date(c.created_at),
                severity=_normalize_severity(c.priority.label if c.priority else None),
                lat=loc.latitude,
                lng=loc.longitude,
                distanceKm=round(d, 1),
            )
        )

    rows.sort(key=lambda r: r.distanceKm)
    return rows[: max(1, min(limit, MAX_NEARBY_RESULTS))]


def get_case_location(
    db: Session,
    *,
    user: User,
    request: Optional[Request],
    case_id: str,
    nearby_radius_km: float = DEFAULT_NEARBY_RADIUS_KM,
    nearby_limit: int = 10,
) -> CaseLocationResponse:
    """Returns everything the Location tab needs in one round-trip."""
    case = _resolve_case(db, user=user, case_id=case_id)
    loc = case.location

    try:
        audit.log_event(
            db,
            user_id=user.id,
            action="VIEW",
            module="Case Management",
            detail=f"Viewed location tab for '{case.case_id}'.",
            target_type="case",
            target_id=case.case_id,
            request=request,
        )
        db.commit()
    except Exception:
        db.rollback()

    if not loc:
        return CaseLocationResponse(
            case_id=case.case_id,
            has_location=False,
            address=None,
            security=SceneSecurity(),
            proximity=ProximityInfo(),
            notes=None,
            nearby=[],
        )

    coords = None
    if loc.latitude is not None and loc.longitude is not None:
        coords = LatLng(lat=loc.latitude, lng=loc.longitude)

    address = CrimeSceneAddress(
        address=_format_full_address(loc),
        area=_format_area_line(loc),
        city=loc.city.name if loc.city else None,
        province=loc.province.label if loc.province else None,
        crimeSceneType=loc.crime_scene_type,
        accessStatus=loc.scene_access,
        coordinates=coords,
    )

    security = _derive_scene_security(db, case)
    proximity = _derive_proximity(loc)

    nearby: List[NearbyCase] = []
    if coords:
        nearby = _find_nearby_cases(
            db,
            parent_case=case,
            center=coords,
            radius_km=nearby_radius_km,
            limit=nearby_limit,
        )

    notes = None

    return CaseLocationResponse(
        case_id=case.case_id,
        has_location=True,
        address=address,
        security=security,
        proximity=proximity,
        notes=notes,
        nearby=nearby,
    )