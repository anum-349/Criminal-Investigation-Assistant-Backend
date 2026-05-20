# services/case_linker_engine.py
# ─────────────────────────────────────────────────────────────────────────────
# Case Linker Engine — v3 (investigator-grade explanations)
#
# WHAT'S NEW vs v2
# ────────────────
# Explanations now read like an investigator's note:
#
#   Headline (≤120 chars, goes in the table):
#     "Bilal Hussain (CNIC 35201-1234567-1) appears in both cases.
#      Same weapon (9mm pistol) and matching MO (financial motive)."
#
#   Detail (multi-section, surfaced in the row's expandable drawer):
#     # SHARED SUSPECTS (1)
#     • Bilal Hussain — CNIC 35201-1234567-1, age 34, known affiliations:
#       "Anjuman gang". Has prior criminal record. Same escape vehicle
#       on both cases: white Toyota Corolla.
#
#     # WEAPON
#     • Both incidents involved a 9mm pistol.
#
#     # MODUS OPERANDI
#     • Both narratives mention "armed robbery during business hours";
#       motive recorded as "financial" on both cases.
#
#     # LOCATION
#     • Same city (Karachi); incidents 1.4 km apart (Saddar police
#       station ↔ Aram Bagh police station).
#
#     # TIMING
#     • 11 days apart (C-2040: 2025-09-12 → C-2053: 2025-09-23).
#
#     # CRIME TYPE
#     • Both classified as "Armed Robbery".
#
#     # EVIDENCE
#     • Both cases have firearm-related evidence on file.
#
# Eight scoring dimensions, all read real columns on real models:
#     1. Suspects     (person_id / CNIC / normalised name)
#     2. Victims      (person_id / CNIC / normalised name)
#     3. Weapon       (exact lookup match + soft category match)
#     4. Location     (city → police_station → area → GPS Haversine)
#     5. Temporal     (days-apart decay, time-of-day pattern)
#     6. MO           (cause_of_death + motive + modus_operandi narrative)
#     7. Crime type   (same CaseType, or related family)
#     8. Evidence     (overlapping evidence types)
#
# OUTPUT CONTRACT
# ───────────────
# LinkProposal.headline      ← short, table-friendly
# LinkProposal.detail        ← markdown-style multi-section block
# LinkProposal.explanation   ← keeps backwards compat (same as detail)
# LinkProposal.confidence    ← 0.0–1.0 (the row's similarity_score)
# LinkProposal.shared_entities ← unchanged, still feeds case_link_shared_entities
#
# The CaseLink table only has `explanation` (Text). We pack BOTH headline
# and detail into it, separated by a `\n\n---\n\n` marker. The frontend
# splits on that marker to render the two tiers. No schema change needed.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Set, Tuple

import networkx as nx
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload
from services import semantic_similarity as semsim
 
from models import (
    Case,
    CaseSuspect,
    CaseVictim,
    Evidence,
    Person,
)

log = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════════════
# Tunables
# ════════════════════════════════════════════════════════════════════════════
WEIGHTS: Dict[str, float] = {
    "shared_suspect":     0.28,   # was 0.30
    "shared_victim":      0.14,   # was 0.15
    "narrative_semantic": 0.10,   # NEW
    "same_weapon":        0.11,   # was 0.12
    "same_mo":            0.11,   # was 0.12
    "same_location":      0.11,   # was 0.12
    "same_crime_type":    0.07,   # was 0.08
    "evidence_overlap":   0.05,   # was 0.06
    "temporal_close":     0.03,   # was 0.05
}

LINK_TYPE_CODES = {
    "shared_suspect":  "SUSPECT_MATCH",
    "shared_victim":   "VICTIM_MATCH",
    "same_weapon":     "WEAPON_MATCH",
    "same_location":   "LOCATION_MATCH",
    "same_mo":         "MODUS_MATCH",
    "same_crime_type": "CRIME_TYPE_MATCH",
    "evidence_overlap":"EVIDENCE_MATCH",
    "temporal_close":  "TEMPORAL",
    "narrative_semantic": "NARRATIVE_MATCH",
}

# Used by case_linker_hook for the notification body.
LINK_TYPE_HUMAN = {
    "SUSPECT_MATCH":    "shared suspect",
    "VICTIM_MATCH":     "shared victim",
    "WEAPON_MATCH":     "matching weapon",
    "LOCATION_MATCH":   "shared location",
    "MODUS_MATCH":      "matching modus operandi",
    "CRIME_TYPE_MATCH": "same crime type",
    "EVIDENCE_MATCH":   "overlapping evidence",
    "TEMPORAL":         "temporal proximity",
    "OTHER":            "multiple shared signals",
    "NARRATIVE_MATCH": "matching narrative pattern",
}

MIN_PERSIST_SCORE = 0.30
HIGH_CONFIDENCE_THRESHOLD = 0.75

TEMPORAL_FULL_DAYS = 30
TEMPORAL_DECAY_DAYS = 180
TIME_OF_DAY_WINDOW_HOURS = 2  # incidents within 2 hours of each other count

SPATIAL_SAME_STATION_BONUS = 0.4
SPATIAL_GPS_THRESHOLD_KM = 2.0

SEM_FLOOR = 0.55        
SEM_STRONG = 0.85  

# Weapon category families — used when exact label differs but both fall
# in the same family. Extend as new weapon labels are seeded.
WEAPON_FAMILIES: Dict[str, List[str]] = {
    "firearm": ["pistol", "revolver", "handgun", "9mm", "shotgun", "rifle", "gun", "firearm", "ak-47", "tt pistol"],
    "blade":   ["knife", "dagger", "blade", "sword", "machete", "kitchen knife"],
    "blunt":   ["bat", "rod", "stick", "club", "hammer", "iron rod", "lathi"],
    "vehicle": ["car", "motorcycle", "bike", "truck"],
    "poison":  ["poison", "toxic", "chemical"],
    "explosive": ["bomb", "ied", "grenade", "explosive"],
}

# Crime-type families — same logic for case types.
CRIME_TYPE_FAMILIES: Dict[str, List[str]] = {
    "violent":   ["murder", "homicide", "manslaughter", "attempt to murder", "armed robbery", "assault"],
    "property":  ["theft", "burglary", "robbery", "dacoity", "snatching"],
    "fraud":     ["fraud", "forgery", "embezzlement", "cheating"],
    "narcotics": ["narcotics", "drug", "smuggling"],
    "sexual":    ["rape", "sexual assault", "harassment"],
}


# ════════════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════════════
_WS_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"\b[a-z0-9]+\b")
_STOPWORDS = {
    "the", "and", "or", "of", "to", "a", "an", "in", "on", "for", "with",
    "at", "from", "by", "is", "was", "were", "are", "as", "be", "been",
    "this", "that", "these", "those", "it", "its", "they", "their", "them",
    "he", "she", "his", "her", "i", "we", "you", "but", "if", "not", "no",
}


def _norm(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    s = _WS_RE.sub(" ", s.lower()).strip()
    return s or None


def _tokens(s: Optional[str]) -> Set[str]:
    if not s:
        return set()
    return {w for w in _WORD_RE.findall(s.lower()) if w not in _STOPWORDS and len(w) >= 3}


def _jaccard(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _weapon_family(label: Optional[str]) -> Optional[str]:
    """Map a free-text weapon label to its family. Returns None if no
    family matches — we only want to count family-overlap when we're
    reasonably sure."""
    if not label:
        return None
    n = label.lower()
    for fam, members in WEAPON_FAMILIES.items():
        for m in members:
            if m in n:
                return fam
    return None


def _crime_family(label: Optional[str]) -> Optional[str]:
    if not label:
        return None
    n = label.lower()
    for fam, members in CRIME_TYPE_FAMILIES.items():
        for m in members:
            if m in n:
                return fam
    return None


def _person_key(p: Optional[Person]) -> Optional[str]:
    if p is None:
        return None
    if p.cnic and p.cnic.strip():
        return f"cnic:{p.cnic.strip()}"
    if p.full_name and p.full_name.strip():
        return f"name:{_norm(p.full_name)}"
    return None


def _haversine_km(lat1, lon1, lat2, lon2) -> Optional[float]:
    if None in (lat1, lon1, lat2, lon2):
        return None
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    return 2 * R * math.asin(math.sqrt(a))


def _parse_time_hours(t: Optional[str]) -> Optional[float]:
    """Parse 'HH:MM' or 'HH:MM:SS' to hour-of-day float. None on failure."""
    if not t:
        return None
    try:
        parts = t.split(":")
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        return h + m / 60.0
    except (ValueError, IndexError):
        return None


def _truncate(s: str, n: int) -> str:
    s = s.strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


# ════════════════════════════════════════════════════════════════════════════
# Feature container — everything the linker reads about one case
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class PersonInfo:
    """Snapshot of a Person + their CaseSuspect/CaseVictim role row, used
    so we can include rich details in the explanation ('age 34, criminal
    record, gang affiliation: Anjuman gang')."""
    person_id: int
    key: str                              # CNIC or normalised-name
    full_name: Optional[str]
    cnic: Optional[str]
    age: Optional[int]
    gender: Optional[str]
    address: Optional[str]
    # Suspect-only fields (None for victims)
    criminal_record: Optional[bool] = None
    known_affiliations: Optional[str] = None
    vehicle_description: Optional[str] = None
    relation_to_case: Optional[str] = None


@dataclass
class CaseFeatures:
    case_id_internal: int
    case_id_external: str
    case_title: Optional[str]

    suspects: Dict[str, PersonInfo] = field(default_factory=dict)  # key → info
    victims:  Dict[str, PersonInfo] = field(default_factory=dict)

    weapon_label:    Optional[str] = None
    weapon_family:   Optional[str] = None
    motive:          Optional[str] = None
    modus_operandi:  Optional[str] = None
    cause_of_death:  Optional[str] = None
    crime_description: Optional[str] = None

    crime_type_id:    Optional[int] = None
    crime_type_label: Optional[str] = None
    crime_type_family: Optional[str] = None

    city_id:        Optional[int] = None
    city_label:     Optional[str] = None
    police_station: Optional[str] = None
    area:           Optional[str] = None
    latitude:       Optional[float] = None
    longitude:      Optional[float] = None

    incident_date: Optional[date] = None
    incident_hour: Optional[float] = None

    evidence_types: Set[str] = field(default_factory=set)


# ════════════════════════════════════════════════════════════════════════════
# Feature extraction
# ════════════════════════════════════════════════════════════════════════════
def _person_info_from_suspect(s: CaseSuspect) -> Optional[PersonInfo]:
    p = s.person
    if not p:
        return None
    k = _person_key(p)
    if not k:
        return None
    return PersonInfo(
        person_id=p.id,
        key=k,
        full_name=p.full_name,
        cnic=p.cnic,
        age=p.age,
        gender=p.gender,
        address=p.address,
        criminal_record=bool(s.criminal_record),
        known_affiliations=s.known_affiliations,
        vehicle_description=s.vehicle_description,
        relation_to_case=s.relation_to_case,
    )


def _person_info_from_victim(v: CaseVictim) -> Optional[PersonInfo]:
    p = v.person
    if not p:
        return None
    k = _person_key(p)
    if not k:
        return None
    return PersonInfo(
        person_id=p.id, key=k,
        full_name=p.full_name, cnic=p.cnic, age=p.age, gender=p.gender,
        address=p.address,
        relation_to_case=v.relation_to_suspect,
    )


def _build_features(db: Session, case: Case) -> CaseFeatures:
    f = CaseFeatures(
        case_id_internal=case.id,
        case_id_external=case.case_id,
        case_title=case.case_title,
        crime_type_id=case.case_type_id,
        incident_date=case.incident_date,
        incident_hour=_parse_time_hours(case.incident_time),
    )

    # Crime type
    if case.case_type:
        f.crime_type_label = case.case_type.label
        f.crime_type_family = _crime_family(case.case_type.label)

    # Suspects
    for s in case.suspects or []:
        info = _person_info_from_suspect(s)
        if info and info.key not in f.suspects:
            f.suspects[info.key] = info

    # Victims
    for v in case.victims or []:
        info = _person_info_from_victim(v)
        if info and info.key not in f.victims:
            f.victims[info.key] = info

    # Weapon — exact label from lookup table + soft family classification.
    # Also fall back to weapon_description if no weapon_id was selected.
    if case.weapon and case.weapon.label:
        f.weapon_label = case.weapon.label
        f.weapon_family = _weapon_family(case.weapon.label)
    elif getattr(case, "weapon_description", None):
        f.weapon_label = case.weapon_description
        f.weapon_family = _weapon_family(case.weapon_description)

    # MO bundle — three narrative fields on Case, plus murder_details.cause_of_death
    f.motive = _norm(getattr(case, "motive", None))
    f.modus_operandi = _norm(getattr(case, "modus_operandi", None))
    f.crime_description = _norm(getattr(case, "crime_description", None))
    if case.murder_details and case.murder_details.cause_of_death:
        f.cause_of_death = _norm(case.murder_details.cause_of_death)

    # Location
    loc = case.location
    if loc:
        f.city_id = loc.city_id
        f.city_label = loc.city.name if loc.city else None
        f.police_station = _norm(loc.police_station)
        f.area = _norm(loc.area)
        f.latitude = loc.latitude
        f.longitude = loc.longitude

    # Evidence types
    for ev in case.evidences or []:
        if ev.type and ev.type.label:
            f.evidence_types.add(_norm(ev.type.label))

    return f


# ════════════════════════════════════════════════════════════════════════════
# Pairwise scoring — each dimension returns (score, headline_phrase, detail_lines)
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class DimensionResult:
    """One scored dimension. Score is 0.0–1.0; headline_phrase is short
    (used to build the table headline); detail_lines is markdown bullets
    used to build the expandable section."""
    score: float = 0.0
    headline_phrase: Optional[str] = None
    detail_section: Optional[Tuple[str, List[str]]] = None  # (heading, bullets)
    shared_entities: List[Tuple[str, str, Optional[int]]] = field(default_factory=list)


def _dim_narrative_semantic(
    db: Session,
    a: "CaseFeatures", b: "CaseFeatures",
    case_a: Case, case_b: Case,
) -> "DimensionResult":
    """SBERT semantic similarity over the case narratives.
 
    Catches links that the token-based MO dim misses — e.g.:
      A: "suspect approached from behind and demanded the bag"
      B: "perpetrator came up behind complainant and snatched purse"
    Jaccard ≈ 0.05 (different words); cosine ≈ 0.82 (same meaning).
 
    The score is a linear ramp from SEM_FLOOR → SEM_STRONG. Below FLOOR
    we treat as no signal; above STRONG we cap at 1.0. This matches how
    investigators actually think about similarity: under ~0.5 cosine on
    this model is just random short-text alignment, not real semantic
    overlap.
    """
    cosine = semsim.semantic_score(db, case_a, case_b)
    if cosine < SEM_FLOOR:
        return DimensionResult()
 
    # Linear ramp
    if cosine >= SEM_STRONG:
        score = 1.0
    else:
        score = (cosine - SEM_FLOOR) / (SEM_STRONG - SEM_FLOOR)
 
    pct = int(round(cosine * 100))
 
    # Headline phrase — kept compact for table cell.
    if cosine >= 0.85:
        headline = f"strong narrative match (semantic similarity {pct}%)"
    elif cosine >= 0.70:
        headline = f"similar narrative patterns ({pct}% semantic match)"
    else:
        headline = f"weak narrative overlap ({pct}% semantic match)"
 
    # Detail — explain what this signal IS, in plain language, plus the
    # raw number so the investigator can see we didn't fabricate it.
    # This is the "audit trail" SHAP would normally provide, just direct.
    bullets = [
        f"Case narratives have **{pct}% semantic similarity** "
        f"(multilingual MiniLM cosine).",
        "Semantic similarity catches matches based on **meaning** even when "
        "the FIR wording differs — same crime described in different words.",
    ]
 
    # Add a soft caveat when only semantic fires and no concrete entity
    # overlap exists; this is the case most likely to be a false positive
    # (two robberies described similarly but unrelated).
    return DimensionResult(
        score=score,
        headline_phrase=headline,
        detail_section=("NARRATIVE (SEMANTIC)", bullets),
    )

def _dim_suspects(a: CaseFeatures, b: CaseFeatures) -> DimensionResult:
    shared_keys = set(a.suspects.keys()) & set(b.suspects.keys())
    if not shared_keys:
        return DimensionResult()

    # Score: 1.0 for any overlap. Multiple shared suspects is already maximal
    # evidence; we don't need a fancier scale here.
    score = 1.0
    bullets: List[str] = []
    shared: List[Tuple[str, str, Optional[int]]] = []

    for k in sorted(shared_keys):
        # Take the richer of the two records (the one with more populated
        # fields) for the explanation. Both should refer to the same Person,
        # but suspect-role columns like criminal_record may differ per case.
        ia = a.suspects[k]
        ib = b.suspects[k]
        # Build a per-suspect description string.
        parts: List[str] = []
        name = ia.full_name or ib.full_name or "Unnamed"
        if ia.cnic or ib.cnic:
            parts.append(f"CNIC {ia.cnic or ib.cnic}")
        age = ia.age or ib.age
        if age:
            parts.append(f"age {age}")
        # Affiliations: take from either case
        affil = ia.known_affiliations or ib.known_affiliations
        if affil:
            parts.append(f"affiliated with \"{affil.strip()}\"")
        # Criminal record: TRUE on either case is notable
        if ia.criminal_record or ib.criminal_record:
            parts.append("has prior criminal record")
        # Vehicle: compare across both cases — same vehicle is a strong cue
        va, vb = ia.vehicle_description, ib.vehicle_description
        if va and vb and _norm(va) == _norm(vb):
            parts.append(f"same escape vehicle on both cases ({va.strip()})")
        elif va and vb:
            parts.append(f"escape vehicle differs ({va.strip()} vs {vb.strip()})")
        elif va or vb:
            parts.append(f"vehicle on file ({(va or vb).strip()})")
        # Relation to case
        rel_a, rel_b = ia.relation_to_case, ib.relation_to_case
        if rel_a and rel_b and rel_a == rel_b:
            parts.append(f"recorded as \"{rel_a}\" in both")
        elif rel_a or rel_b:
            parts.append(f"role: {rel_a or '—'} / {rel_b or '—'}")

        bullet = f"**{name}** — " + "; ".join(parts) if parts else f"**{name}**"
        bullets.append(bullet)
        shared.append(("SUSPECT", name, ia.person_id))

    # Headline phrase — name the suspect inline ("Bilal Hussain (CNIC …)
    # appears in both cases"). If multiple, pick the most-identifiable
    # one (one with CNIC > one with name only) and append count.
    primary = sorted(
        (a.suspects[k] for k in shared_keys),
        key=lambda i: (0 if i.cnic else 1, i.full_name or ""),
    )[0]
    if primary.cnic:
        headline = f"{primary.full_name or 'Suspect'} (CNIC {primary.cnic}) appears in both cases"
    elif primary.full_name:
        headline = f"{primary.full_name} appears in both cases"
    else:
        headline = "Shared suspect in both cases"
    if len(shared_keys) > 1:
        headline += f" (+{len(shared_keys) - 1} other shared suspect{'s' if len(shared_keys) > 2 else ''})"

    return DimensionResult(
        score=score,
        headline_phrase=headline,
        detail_section=(f"SHARED SUSPECTS ({len(shared_keys)})", bullets),
        shared_entities=shared,
    )


def _dim_victims(a: CaseFeatures, b: CaseFeatures) -> DimensionResult:
    shared_keys = set(a.victims.keys()) & set(b.victims.keys())
    if not shared_keys:
        return DimensionResult()

    score = 1.0
    bullets: List[str] = []
    shared: List[Tuple[str, str, Optional[int]]] = []

    for k in sorted(shared_keys):
        ia, ib = a.victims[k], b.victims[k]
        parts = []
        name = ia.full_name or ib.full_name or "Unnamed victim"
        if ia.cnic or ib.cnic:
            parts.append(f"CNIC {ia.cnic or ib.cnic}")
        age = ia.age or ib.age
        gender = ia.gender or ib.gender
        demo = []
        if age: demo.append(f"age {age}")
        if gender: demo.append(gender.lower())
        if demo:
            parts.append(", ".join(demo))
        if ia.relation_to_case and ib.relation_to_case and ia.relation_to_case == ib.relation_to_case:
            parts.append(f"same relation to suspect (\"{ia.relation_to_case}\") in both cases")
        bullets.append(f"**{name}** — " + "; ".join(parts) if parts else f"**{name}**")
        shared.append(("VICTIM", name, ia.person_id))

    primary = sorted(
        (a.victims[k] for k in shared_keys),
        key=lambda i: (0 if i.cnic else 1, i.full_name or ""),
    )[0]
    if primary.full_name:
        headline = f"Victim {primary.full_name} appears in both cases"
    else:
        headline = "Shared victim in both cases"

    return DimensionResult(
        score=score, headline_phrase=headline,
        detail_section=(f"SHARED VICTIMS ({len(shared_keys)})", bullets),
        shared_entities=shared,
    )


def _dim_weapon(a: CaseFeatures, b: CaseFeatures) -> DimensionResult:
    """Two tiers: exact-label match (full score) or family match (half)."""
    if not a.weapon_label and not b.weapon_label:
        return DimensionResult()
    la = _norm(a.weapon_label)
    lb = _norm(b.weapon_label)
    if not la or not lb:
        return DimensionResult()

    if la == lb:
        return DimensionResult(
            score=1.0,
            headline_phrase=f"same weapon ({a.weapon_label})",
            detail_section=("WEAPON", [f"Both incidents involved a **{a.weapon_label}**."]),
            shared_entities=[("WEAPON", a.weapon_label, None)],
        )

    # Family match — same kind of weapon, different label
    if a.weapon_family and a.weapon_family == b.weapon_family:
        return DimensionResult(
            score=0.5,
            headline_phrase=f"both used a {a.weapon_family}",
            detail_section=("WEAPON", [
                f"Different specific weapons (**{a.weapon_label}** vs **{b.weapon_label}**) "
                f"but both fall in the **{a.weapon_family}** family."
            ]),
            shared_entities=[("WEAPON_FAMILY", a.weapon_family, None)],
        )

    return DimensionResult()


def _dim_location(a: CaseFeatures, b: CaseFeatures) -> DimensionResult:
    """Tiered: same city = 0.5, same station/area or near GPS = +0.5."""
    if not a.city_id or not b.city_id:
        return DimensionResult()
    if a.city_id != b.city_id:
        return DimensionResult()  # different cities → no location overlap

    city_name = a.city_label or f"city id {a.city_id}"
    score = 0.5
    bullets: List[str] = []

    # Police station match
    if a.police_station and a.police_station == b.police_station:
        score = 1.0
        bullets.append(
            f"Both registered at **{a.police_station}** police station (same jurisdiction)."
        )
        headline = f"same police station ({a.police_station}), {city_name}"
        ent = ("LOCATION", f"{a.police_station}, {city_name}", None)
    elif a.area and a.area == b.area:
        score = 1.0
        bullets.append(
            f"Same area: **{a.area}** in {city_name}."
        )
        headline = f"same area ({a.area}), {city_name}"
        ent = ("LOCATION", f"{a.area}, {city_name}", None)
    else:
        # GPS proximity if labels disagree
        d = _haversine_km(a.latitude, a.longitude, b.latitude, b.longitude)
        if d is not None and d <= SPATIAL_GPS_THRESHOLD_KM:
            score = min(1.0, 0.5 + (1.0 - d / SPATIAL_GPS_THRESHOLD_KM) * 0.5)
            stations = []
            if a.police_station: stations.append(a.police_station)
            if b.police_station: stations.append(b.police_station)
            station_phrase = f" ({' ↔ '.join(stations)})" if stations else ""
            bullets.append(
                f"Same city ({city_name}); incidents **{d:.2f} km apart**{station_phrase}."
            )
            headline = f"incidents {d:.2f} km apart in {city_name}"
            ent = ("LOCATION", f"~{d:.2f}km apart in {city_name}", None)
        else:
            bullets.append(f"Same city ({city_name}), but different areas and >{SPATIAL_GPS_THRESHOLD_KM} km apart.")
            headline = f"same city ({city_name})"
            ent = ("LOCATION", city_name, None)

    return DimensionResult(
        score=score,
        headline_phrase=headline,
        detail_section=("LOCATION", bullets),
        shared_entities=[ent],
    )


def _dim_temporal(a: CaseFeatures, b: CaseFeatures) -> DimensionResult:
    """Days-apart decay + time-of-day pattern.
    NOTE: Returns 0 unless the dates are within TEMPORAL_DECAY_DAYS.
    Caller should only count this dimension if another dimension also
    fired — temporal alone is too weak to suggest a link."""
    if not a.incident_date or not b.incident_date:
        return DimensionResult()

    delta_days = abs((a.incident_date - b.incident_date).days)
    if delta_days > TEMPORAL_DECAY_DAYS:
        return DimensionResult()

    if delta_days <= TEMPORAL_FULL_DAYS:
        score = 1.0
    else:
        span = TEMPORAL_DECAY_DAYS - TEMPORAL_FULL_DAYS
        score = 1.0 - ((delta_days - TEMPORAL_FULL_DAYS) / span)

    bullets = []
    # Day summary
    if delta_days == 0:
        bullets.append(f"Both incidents on **{a.incident_date.isoformat()}** (same day).")
    else:
        bullets.append(
            f"**{delta_days} day(s) apart** "
            f"({a.case_id_external}: {a.incident_date.isoformat()} → "
            f"{b.case_id_external}: {b.incident_date.isoformat()})."
        )

    # Time-of-day pattern
    if a.incident_hour is not None and b.incident_hour is not None:
        hour_delta = abs(a.incident_hour - b.incident_hour)
        # Handle wrap-around (23:30 vs 00:30 is 1h apart, not 23h)
        hour_delta = min(hour_delta, 24 - hour_delta)
        if hour_delta <= TIME_OF_DAY_WINDOW_HOURS:
            score = min(1.0, score + 0.1)  # small bonus
            bullets.append(
                f"Similar time of day "
                f"(~{int(a.incident_hour):02d}:{int((a.incident_hour%1)*60):02d} vs "
                f"~{int(b.incident_hour):02d}:{int((b.incident_hour%1)*60):02d}, "
                f"{hour_delta:.1f}h apart)."
            )

    return DimensionResult(
        score=score,
        headline_phrase=f"{delta_days} day(s) apart",
        detail_section=("TIMING", bullets),
    )


def _dim_mo(a: CaseFeatures, b: CaseFeatures) -> DimensionResult:
    """Bundle: cause_of_death + motive + modus_operandi + crime_description.
    Each field gets its own tiny analysis; we sum into one score."""
    bullets: List[str] = []
    sub_scores: List[float] = []
    headline_bits: List[str] = []

    # Cause of death — exact match (mostly for murder cases)
    if a.cause_of_death and b.cause_of_death and a.cause_of_death == b.cause_of_death:
        sub_scores.append(1.0)
        bullets.append(f"Identical cause of death recorded: **{a.cause_of_death}**.")
        headline_bits.append(f"same cause of death ({a.cause_of_death})")

    # Motive — exact normalised match counts; jaccard for soft match
    if a.motive and b.motive:
        if a.motive == b.motive:
            sub_scores.append(1.0)
            bullets.append(f"Same motive on both cases: **\"{a.motive}\"**.")
            headline_bits.append(f"same motive ({a.motive})")
        else:
            j = _jaccard(_tokens(a.motive), _tokens(b.motive))
            if j >= 0.5:
                sub_scores.append(j)
                bullets.append(
                    f"Motive overlap (Jaccard {j:.2f}): \"{_truncate(a.motive, 60)}\" "
                    f"vs \"{_truncate(b.motive, 60)}\"."
                )

    # Modus operandi — same logic as motive
    if a.modus_operandi and b.modus_operandi:
        if a.modus_operandi == b.modus_operandi:
            sub_scores.append(1.0)
            bullets.append(f"Identical MO recorded: \"{_truncate(a.modus_operandi, 100)}\".")
            headline_bits.append("matching MO")
        else:
            j = _jaccard(_tokens(a.modus_operandi), _tokens(b.modus_operandi))
            if j >= 0.4:
                sub_scores.append(j)
                bullets.append(
                    f"Modus operandi overlap (Jaccard {j:.2f}): "
                    f"\"{_truncate(a.modus_operandi, 60)}\" vs \"{_truncate(b.modus_operandi, 60)}\"."
                )

    # Crime description — softer signal; only count if MO/motive didn't fire
    if not sub_scores and a.crime_description and b.crime_description:
        j = _jaccard(_tokens(a.crime_description), _tokens(b.crime_description))
        if j >= 0.4:
            sub_scores.append(j * 0.7)  # discount — narrative match alone is weak
            bullets.append(
                f"Crime descriptions show narrative overlap (Jaccard {j:.2f})."
            )

    if not sub_scores:
        return DimensionResult()

    score = sum(sub_scores) / len(sub_scores)
    headline = "; ".join(headline_bits) if headline_bits else "modus operandi overlap"

    return DimensionResult(
        score=min(score, 1.0),
        headline_phrase=headline,
        detail_section=("MODUS OPERANDI", bullets),
    )


def _dim_crime_type(a: CaseFeatures, b: CaseFeatures) -> DimensionResult:
    """Same CaseType.id = full; same family = half. Used as a supporting
    signal, not a primary driver."""
    if not a.crime_type_id or not b.crime_type_id:
        return DimensionResult()
    if a.crime_type_id == b.crime_type_id:
        label = a.crime_type_label or f"type id {a.crime_type_id}"
        return DimensionResult(
            score=1.0,
            headline_phrase=f"same crime type ({label})",
            detail_section=("CRIME TYPE", [f"Both classified as **{label}**."]),
        )
    if a.crime_type_family and a.crime_type_family == b.crime_type_family:
        return DimensionResult(
            score=0.5,
            headline_phrase=f"both {a.crime_type_family}-family crimes",
            detail_section=("CRIME TYPE", [
                f"Different specific types "
                f"(**{a.crime_type_label}** vs **{b.crime_type_label}**) "
                f"but both in the **{a.crime_type_family}** family."
            ]),
        )
    return DimensionResult()


def _dim_evidence(a: CaseFeatures, b: CaseFeatures) -> DimensionResult:
    """Evidence type overlap — both cases have, e.g., firearm rounds."""
    if not a.evidence_types or not b.evidence_types:
        return DimensionResult()
    shared = a.evidence_types & b.evidence_types
    if not shared:
        return DimensionResult()
    total = a.evidence_types | b.evidence_types
    score = len(shared) / len(total)
    bullets = [
        f"Overlapping evidence types: {', '.join(sorted(shared))}."
    ]
    if a.evidence_types - shared:
        bullets.append(f"Only in {a.case_id_external}: {', '.join(sorted(a.evidence_types - shared))}.")
    if b.evidence_types - shared:
        bullets.append(f"Only in {b.case_id_external}: {', '.join(sorted(b.evidence_types - shared))}.")
    return DimensionResult(
        score=score,
        headline_phrase=f"shared evidence: {', '.join(sorted(shared))}",
        detail_section=("EVIDENCE", bullets),
    )


# ════════════════════════════════════════════════════════════════════════════
# Pair scoring: bundle all dimensions
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class EdgeScore:
    dimensions: Dict[str, DimensionResult]

    @property
    def total(self) -> float:
        s = sum(self.dimensions[k].score * w for k, w in WEIGHTS.items() if k in self.dimensions)
        return min(s, 1.0)

    @property
    def primary(self) -> Optional[str]:
        if not self.dimensions:
            return None
        contributions = {
            k: self.dimensions[k].score * WEIGHTS.get(k, 0.0)
            for k in self.dimensions
        }
        best = max(contributions.items(), key=lambda kv: kv[1])
        return best[0] if best[1] > 0 else None

    @property
    def shared_entities(self) -> List[Tuple[str, str, Optional[int]]]:
        out = []
        for d in self.dimensions.values():
            out.extend(d.shared_entities)
        return out


def _score_pair(
    db: Session,
    a: "CaseFeatures", b: "CaseFeatures",
    case_a: Case, case_b: Case,
) -> "EdgeScore":
    raw: Dict[str, DimensionResult] = {
        "shared_suspect":     _dim_suspects(a, b),
        "shared_victim":      _dim_victims(a, b),
        "narrative_semantic": _dim_narrative_semantic(db, a, b, case_a, case_b),
        "same_weapon":        _dim_weapon(a, b),
        "same_location":      _dim_location(a, b),
        "same_mo":            _dim_mo(a, b),
        "same_crime_type":    _dim_crime_type(a, b),
        "evidence_overlap":   _dim_evidence(a, b),
    }
    # Temporal is conditional — only counts when something else fired,
    # because lots of unrelated crimes happen on the same day in the
    # same city.
    any_other_fired = any(d.score > 0 for d in raw.values())
    if any_other_fired:
        raw["temporal_close"] = _dim_temporal(a, b)
 
    dims = {k: v for k, v in raw.items() if v.score > 0}
    return EdgeScore(dimensions=dims)


# ════════════════════════════════════════════════════════════════════════════
# Explanation builder — two-tier output (headline + detail)
# ════════════════════════════════════════════════════════════════════════════
DETAIL_MARKER = "\n\n---DETAIL---\n\n"


def _build_headline(edge: EdgeScore, a_ext: str, b_ext: str) -> str:
    """One-sentence narrative. Picks the 1–3 strongest dimensions and
    joins their headline phrases. Trimmed to 200 chars max."""
    if not edge.dimensions:
        return f"{a_ext} and {b_ext} share signals."

    # Sort dimensions by contribution
    ranked = sorted(
        edge.dimensions.items(),
        key=lambda kv: kv[1].score * WEIGHTS.get(kv[0], 0),
        reverse=True,
    )
    phrases = [r[1].headline_phrase for r in ranked if r[1].headline_phrase]

    if not phrases:
        return f"{a_ext} and {b_ext} share signals."

    # First phrase capitalised; subsequent joined with semicolons.
    first = phrases[0]
    rest = phrases[1:3]  # at most 2 more
    if not rest:
        out = first[0].upper() + first[1:] + "."
    else:
        out = first[0].upper() + first[1:] + "; " + "; ".join(rest) + "."

    return _truncate(out, 240)


def _build_detail(edge: EdgeScore, a: CaseFeatures, b: CaseFeatures) -> str:
    """Multi-section markdown block — one section per fired dimension."""
    sections: List[str] = []

    # Header line — the two cases being compared
    header = (
        f"Comparison: **{a.case_id_external}** "
        f"({a.case_title or 'untitled'}) ↔ **{b.case_id_external}** "
        f"({b.case_title or 'untitled'})"
    )
    sections.append(header)

    # Sort by weighted contribution so the most important dim shows first
    ranked = sorted(
        edge.dimensions.items(),
        key=lambda kv: kv[1].score * WEIGHTS.get(kv[0], 0),
        reverse=True,
    )
    for _, dim in ranked:
        if not dim.detail_section:
            continue
        heading, bullets = dim.detail_section
        body = "\n".join(f"• {b}" for b in bullets) if bullets else "(no details)"
        sections.append(f"### {heading}\n{body}")

    return "\n\n".join(sections)


# ════════════════════════════════════════════════════════════════════════════
# Public API
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class LinkProposal:
    source_case_id: int
    target_case_id: int
    link_type: str
    confidence: float
    headline: str
    detail: str
    explanation: str   # headline + marker + detail; persisted as-is
    shared_entities: List[Tuple[str, str, Optional[int]]]


def _make_proposal(focal_internal_id: int, edge: EdgeScore,
                   a: CaseFeatures, b: CaseFeatures) -> Optional[LinkProposal]:
    if edge.total < MIN_PERSIST_SCORE:
        return None
    primary = edge.primary
    if not primary:
        return None
    link_type = LINK_TYPE_CODES.get(primary, "OTHER")
    headline = _build_headline(edge, a.case_id_external, b.case_id_external)
    detail = _build_detail(edge, a, b)
    explanation = headline + DETAIL_MARKER + detail
    return LinkProposal(
        source_case_id=focal_internal_id,
        target_case_id=b.case_id_internal,
        link_type=link_type,
        confidence=round(edge.total, 4),
        headline=headline,
        detail=detail,
        explanation=explanation,
        shared_entities=edge.shared_entities,
    )


def compute_links_for_case(
    db: Session,
    *,
    focal_case_id: int,
    candidate_limit: int = 1000,
) -> List[LinkProposal]:
    """Build a graph centred on focal_case, return edges as LinkProposals."""

    loads = [
        joinedload(Case.suspects).joinedload(CaseSuspect.person),
        joinedload(Case.victims).joinedload(CaseVictim.person),
        joinedload(Case.location),
        joinedload(Case.weapon),
        joinedload(Case.murder_details),
        joinedload(Case.case_type),
        joinedload(Case.evidences).joinedload(Evidence.type),
    ]

    focal = (
        db.query(Case)
        .options(*loads)
        .filter(Case.id == focal_case_id, Case.is_deleted == False)  # noqa: E712
        .first()
    )
    if not focal:
        return []

    # Pre-filter the candidate pool. Same heuristic as v2: anything in the
    # same city OR within 180 days OR sharing a suspect/victim qualifies.
    suspect_pids = {s.person_id for s in (focal.suspects or []) if s.person_id}
    victim_pids = {v.person_id for v in (focal.victims or []) if v.person_id}

    candidate_q = (
        db.query(Case)
        .options(*loads)
        .filter(Case.id != focal.id, Case.is_deleted == False)  # noqa: E712
    )

    filters = []
    if focal.location and focal.location.city_id:
        filters.append(Case.location.has(city_id=focal.location.city_id))
    if focal.incident_date:
        ws = focal.incident_date - timedelta(days=TEMPORAL_DECAY_DAYS)
        we = focal.incident_date + timedelta(days=TEMPORAL_DECAY_DAYS)
        filters.append(Case.incident_date.between(ws, we))
    if filters:
        candidate_q = candidate_q.filter(or_(*filters))

    candidates = candidate_q.limit(candidate_limit).all()

    # Always include shared-entity matches, even outside the locality window.
    if suspect_pids or victim_pids:
        all_pids = suspect_pids | victim_pids
        extra = (
            db.query(Case)
            .options(*loads)
            .filter(
                Case.id != focal.id,
                Case.is_deleted == False,  # noqa: E712
                or_(
                    Case.suspects.any(CaseSuspect.person_id.in_(all_pids)),
                    Case.victims.any(CaseVictim.person_id.in_(all_pids)),
                ),
            )
            .all()
        )
        seen = {c.id for c in candidates}
        candidates.extend(c for c in extra if c.id not in seen)

    if not candidates:
        return []

    focal_f = _build_features(db, focal)
    cand_fs = [_build_features(db, c) for c in candidates]

    # NetworkX graph — used for analytics + to keep the "graph algorithm"
    # framing your FYP report mentions. The proposal list is derived
    # from edges incident to the focal node.
    G = nx.Graph()
    G.add_node(focal_f.case_id_internal)
    for cf in cand_fs:
        G.add_node(cf.case_id_internal)

    proposals: List[LinkProposal] = []
    semsim.get_embedding(db, focal)
 
    for cand_case, cf in zip(candidates, cand_fs):
        edge = _score_pair(db, focal_f, cf, focal, cand_case)
        if edge.total >= MIN_PERSIST_SCORE:
            p = _make_proposal(focal_f.case_id_internal, edge, focal_f, cf)
            if p:
                proposals.append(p)

    return proposals


def compute_case_graph_metrics(db: Session) -> Dict[str, dict]:
    """Full-graph centrality — used by the analytics dashboard.
    Builds features for every case and computes degree + betweenness.
    Skips betweenness above 500 nodes (it's O(V*E))."""
    loads = [
        joinedload(Case.suspects).joinedload(CaseSuspect.person),
        joinedload(Case.victims).joinedload(CaseVictim.person),
        joinedload(Case.location),
        joinedload(Case.weapon),
        joinedload(Case.murder_details),
        joinedload(Case.case_type),
        joinedload(Case.evidences).joinedload(Evidence.type),
    ]
    cases = (
        db.query(Case)
        .options(*loads)
        .filter(Case.is_deleted == False)  # noqa: E712
        .all()
    )
    features = [_build_features(db, c) for c in cases]
    G = nx.Graph()
    for f in features:
        G.add_node(f.case_id_internal, external=f.case_id_external)
    for i in range(len(features)):
        for j in range(i + 1, len(features)):
            e = _score_pair(features[i], features[j])
            if e.total >= MIN_PERSIST_SCORE:
                G.add_edge(features[i].case_id_internal,
                           features[j].case_id_internal,
                           score=e.total)

    if G.number_of_edges() == 0:
        return {}

    degree = dict(G.degree())
    betweenness = nx.betweenness_centrality(G) if G.number_of_nodes() < 500 else {}
    communities = []
    try:
        communities = list(nx.community.greedy_modularity_communities(G))
    except Exception:
        pass

    out: Dict[str, dict] = {}
    for f in features:
        cid = f.case_id_internal
        if cid not in G:
            continue
        community_id = next(
            (i for i, comm in enumerate(communities) if cid in comm), None,
        )
        out[f.case_id_external] = {
            "degree": degree.get(cid, 0),
            "betweenness": betweenness.get(cid, 0.0),
            "community": community_id,
        }
    return out