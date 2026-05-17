from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Set, Tuple

import networkx as nx
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from models import (
    Case,
    CaseLink,
    CaseLinkSharedEntity,
    CaseSuspect,
    CaseVictim,
    Person,
    Location,
    MurderDetails,
)

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Tunables
# ─────────────────────────────────────────────────────────────────────────────
# Feature weights — sum doesn't need to be 1.0, we normalise at the end.
# Suspect/victim overlap is by far the strongest evidence in real
# investigations, so it dominates. Temporal proximity is the weakest signal
# alone (lots of crimes happen on the same day in the same city) but
# combined with other features it bumps confidence meaningfully.
WEIGHTS: Dict[str, float] = {
    "shared_suspect":  0.35,
    "shared_victim":   0.20,
    "same_weapon":     0.15,
    "same_location":   0.15,
    "same_mo":         0.10,
    "temporal_close":  0.05,
}

# Link type LABELS used when we write to `case_links.link_type`. These must
# match the keys in `case_linked_service.LINK_TYPE_LABEL` so the frontend
# shows the right pill colour. Keep in sync.
LINK_TYPE_CODES = {
    "shared_suspect":  "SAME_SUSPECT",
    "shared_victim":   "SAME_VICTIM",
    "same_weapon":     "SAME_WEAPON",
    "same_location":   "SAME_LOCATION",
    "same_mo":         "SAME_MO",
    "temporal_close":  "TEMPORAL",
}

# A pair has to score at least this much to even be persisted as a link.
# Below this, we treat it as noise. Tune via env var if needed.
MIN_PERSIST_SCORE = 0.30

# Pairs with score >= this trigger a notification.
HIGH_CONFIDENCE_THRESHOLD = 0.75

# Temporal proximity: incidents within 30 days score full, within 90 days
# decay linearly to 0.
TEMPORAL_FULL_DAYS = 30
TEMPORAL_DECAY_DAYS = 90

# Spatial proximity: same city contributes; same police_station + area
# scores higher.
SPATIAL_SAME_AREA_BONUS = 0.4   # added on top of base same_location 0.6


# ─────────────────────────────────────────────────────────────────────────────
# Feature container
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class CaseFeatures:
    """Pre-computed feature bundle for one case. Building these once per
    case and reusing for every pair is what makes the O(N) pass cheap."""
    case_id_internal: int
    case_id_external: str

    # Entity fingerprints (lowercased, stripped)
    suspect_keys: Set[str] = field(default_factory=set)   # cnic|name fallback
    victim_keys:  Set[str] = field(default_factory=set)
    suspect_person_ids: Set[int] = field(default_factory=set)
    victim_person_ids:  Set[int] = field(default_factory=set)

    # Crime details
    weapon_key:      Optional[str] = None   # normalised weapon label
    cause_of_death:  Optional[str] = None   # for MO comparison on murders
    crime_type_id:   Optional[int] = None

    # Location
    city_id:           Optional[int] = None
    police_station:    Optional[str] = None
    area:              Optional[str] = None
    latitude:          Optional[float] = None
    longitude:         Optional[float] = None

    # Temporal
    incident_date: Optional[date] = None


# ─────────────────────────────────────────────────────────────────────────────
# Feature extraction
# ─────────────────────────────────────────────────────────────────────────────
def _norm(s: Optional[str]) -> Optional[str]:
    """Lowercase + collapse whitespace. None stays None.
    Used everywhere we compare free-text fields between cases."""
    if s is None:
        return None
    s = " ".join(s.lower().split())
    return s or None


def _person_key(p: Optional[Person]) -> Optional[str]:
    """Stable identifier for a person across cases.
    Prefer CNIC (unique national ID), fall back to normalised full name.
    Two cases will share a suspect if either matches."""
    if p is None:
        return None
    if p.cnic and p.cnic.strip():
        return f"cnic:{p.cnic.strip()}"
    if p.full_name and p.full_name.strip():
        return f"name:{_norm(p.full_name)}"
    return None


def _build_features(db: Session, case: Case) -> CaseFeatures:
    """Pull everything we need for one case in one round-trip.
    Eager-loaded relationships so this doesn't lazy-load 20 extra queries
    per case when the engine runs on a large graph."""
    f = CaseFeatures(
        case_id_internal=case.id,
        case_id_external=case.case_id,
        crime_type_id=case.case_type_id,
        incident_date=case.incident_date,
    )

    # ── Suspects ──────────────────────────────────────────────────────────
    for s in case.suspects or []:
        p = s.person
        if not p:
            continue
        k = _person_key(p)
        if k:
            f.suspect_keys.add(k)
        f.suspect_person_ids.add(p.id)

    # ── Victims ───────────────────────────────────────────────────────────
    for v in case.victims or []:
        p = v.person
        if not p:
            continue
        k = _person_key(p)
        if k:
            f.victim_keys.add(k)
        f.victim_person_ids.add(p.id)

    # ── Weapon (from Case.weapon_id lookup) ───────────────────────────────
    if case.weapon and case.weapon.label:
        f.weapon_key = _norm(case.weapon.label)

    # ── Location ──────────────────────────────────────────────────────────
    loc = case.location  # 1:1 via Location.case_id_fk; SQLAlchemy backref
    if loc:
        f.city_id        = loc.city_id
        f.police_station = _norm(loc.police_station)
        f.area           = _norm(loc.area)
        f.latitude       = loc.latitude
        f.longitude      = loc.longitude

    # ── MO (currently only murder cases have rich MO data) ────────────────
    if case.murder_details:
        f.cause_of_death = _norm(case.murder_details.cause_of_death)

    return f


# ─────────────────────────────────────────────────────────────────────────────
# Pairwise scoring
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class EdgeScore:
    """Result of scoring two cases against each other."""
    feature_scores: Dict[str, float]              # raw 0-1 per feature
    shared_entities: List[Tuple[str, str, Optional[int]]]  # (type, value, person_id)
    reasons: List[str]                            # human-readable bullets

    @property
    def total(self) -> float:
        """Weighted sum, capped at 1.0."""
        s = sum(self.feature_scores.get(k, 0.0) * w for k, w in WEIGHTS.items())
        return min(s, 1.0)

    @property
    def primary_feature(self) -> Optional[str]:
        """The feature that contributed most to the total — used as the
        canonical link_type written to the DB."""
        if not self.feature_scores:
            return None
        contributions = {
            k: self.feature_scores.get(k, 0.0) * WEIGHTS.get(k, 0.0)
            for k in WEIGHTS
        }
        # max by contribution; ties broken by WEIGHTS order (dict insertion)
        best = max(contributions.items(), key=lambda kv: kv[1])
        return best[0] if best[1] > 0 else None


def _temporal_score(d1: Optional[date], d2: Optional[date]) -> float:
    """Linear decay: 1.0 if within 30 days, 0.0 beyond 90 days."""
    if not d1 or not d2:
        return 0.0
    delta = abs((d1 - d2).days)
    if delta <= TEMPORAL_FULL_DAYS:
        return 1.0
    if delta >= TEMPORAL_DECAY_DAYS:
        return 0.0
    span = TEMPORAL_DECAY_DAYS - TEMPORAL_FULL_DAYS
    return 1.0 - ((delta - TEMPORAL_FULL_DAYS) / span)


def _haversine_km(lat1, lon1, lat2, lon2) -> Optional[float]:
    """Great-circle distance in km. None if any coord is missing."""
    if None in (lat1, lon1, lat2, lon2):
        return None
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    return 2 * R * math.asin(math.sqrt(a))


def _score_pair(a: CaseFeatures, b: CaseFeatures) -> EdgeScore:
    """Compute every feature score between two cases.
    Each feature returns a number in [0, 1] representing how strongly
    THAT feature suggests a link."""
    scores: Dict[str, float] = {}
    shared: List[Tuple[str, str, Optional[int]]] = []
    reasons: List[str] = []

    # ── 1. Shared suspect ─────────────────────────────────────────────────
    shared_suspects = a.suspect_keys & b.suspect_keys
    if shared_suspects:
        # Score is 1.0 if any overlap; multiple overlaps don't multiply
        # because two cases with one shared suspect is already strong evidence.
        scores["shared_suspect"] = 1.0
        # Find the actual person_ids for the shared keys so we can write
        # them to case_link_shared_entities.
        shared_pids = a.suspect_person_ids & b.suspect_person_ids
        for k in shared_suspects:
            pid = next(iter(shared_pids), None) if shared_pids else None
            display = k.split(":", 1)[1] if ":" in k else k
            shared.append(("SUSPECT", display, pid))
        reasons.append(f"{len(shared_suspects)} shared suspect(s)")

    # ── 2. Shared victim ──────────────────────────────────────────────────
    shared_victims = a.victim_keys & b.victim_keys
    if shared_victims:
        scores["shared_victim"] = 1.0
        shared_pids = a.victim_person_ids & b.victim_person_ids
        for k in shared_victims:
            pid = next(iter(shared_pids), None) if shared_pids else None
            display = k.split(":", 1)[1] if ":" in k else k
            shared.append(("VICTIM", display, pid))
        reasons.append(f"{len(shared_victims)} shared victim(s)")

    # ── 3. Same weapon ────────────────────────────────────────────────────
    if a.weapon_key and a.weapon_key == b.weapon_key:
        scores["same_weapon"] = 1.0
        shared.append(("WEAPON", a.weapon_key, None))
        reasons.append(f"same weapon: {a.weapon_key}")

    # ── 4. Same location ──────────────────────────────────────────────────
    # Tiered: same city = 0.6 base, same police_station/area = +0.4.
    # GPS within 1 km counts as same area even if labels disagree.
    loc_score = 0.0
    if a.city_id and a.city_id == b.city_id:
        loc_score = 0.6
        # Tighter signals
        if a.police_station and a.police_station == b.police_station:
            loc_score += SPATIAL_SAME_AREA_BONUS
            shared.append(("LOCATION", a.police_station, None))
            reasons.append(f"same police station: {a.police_station}")
        elif a.area and a.area == b.area:
            loc_score += SPATIAL_SAME_AREA_BONUS
            shared.append(("LOCATION", a.area, None))
            reasons.append(f"same area: {a.area}")
        else:
            # GPS proximity fallback
            d = _haversine_km(a.latitude, a.longitude, b.latitude, b.longitude)
            if d is not None and d <= 1.0:
                loc_score += SPATIAL_SAME_AREA_BONUS
                shared.append(("LOCATION", f"~{d:.2f}km apart", None))
                reasons.append(f"locations within {d:.2f} km")
            else:
                shared.append(("LOCATION", f"same city (id={a.city_id})", None))
                reasons.append("same city")
    if loc_score > 0:
        scores["same_location"] = min(loc_score, 1.0)

    # ── 5. Same MO ────────────────────────────────────────────────────────
    # Currently MO = cause_of_death for murder cases. Easy to extend with
    # the entity_extractor's narrative when that's wired through.
    if a.cause_of_death and a.cause_of_death == b.cause_of_death:
        scores["same_mo"] = 1.0
        shared.append(("MO", a.cause_of_death, None))
        reasons.append(f"same modus operandi: {a.cause_of_death}")

    # ── 6. Temporal proximity ─────────────────────────────────────────────
    # Only fire if at least one OTHER feature already scored — temporal
    # alone is too weak to count. This avoids "every case in the country
    # last Tuesday" false positives.
    t = _temporal_score(a.incident_date, b.incident_date)
    if t > 0 and any(scores.values()):
        scores["temporal_close"] = t
        if a.incident_date and b.incident_date:
            delta = abs((a.incident_date - b.incident_date).days)
            reasons.append(f"incidents {delta} day(s) apart")

    return EdgeScore(feature_scores=scores, shared_entities=shared, reasons=reasons)


# ─────────────────────────────────────────────────────────────────────────────
# Graph builder (the NetworkX part)
# ─────────────────────────────────────────────────────────────────────────────
def _build_graph(features: List[CaseFeatures]) -> nx.Graph:
    """Construct a NetworkX graph from pre-computed features.
    Nodes = cases. Edges = candidate links with the EdgeScore as data."""
    G = nx.Graph()
    for f in features:
        G.add_node(f.case_id_internal, external=f.case_id_external)

    for i in range(len(features)):
        for j in range(i + 1, len(features)):
            edge = _score_pair(features[i], features[j])
            if edge.total >= MIN_PERSIST_SCORE:
                G.add_edge(
                    features[i].case_id_internal,
                    features[j].case_id_internal,
                    score=edge.total,
                    primary=edge.primary_feature,
                    shared=edge.shared_entities,
                    reasons=edge.reasons,
                )
    return G


# ─────────────────────────────────────────────────────────────────────────────
# Public entry points
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class LinkProposal:
    """One proposed link, ready to upsert into case_links."""
    source_case_id: int
    target_case_id: int
    link_type: str              # e.g. "SAME_SUSPECT"
    confidence: float           # 0.0 – 1.0
    explanation: str            # joined reasons
    shared_entities: List[Tuple[str, str, Optional[int]]]


def compute_links_for_case(
    db: Session,
    *,
    focal_case_id: int,
    candidate_limit: int = 500,
) -> List[LinkProposal]:
    """Build a graph of (focal case + every other live case), return all
    edges incident to the focal case as LinkProposals.

    candidate_limit caps the pool — for a typical FYP-scale dataset (a
    few hundred cases) the cap never fires. For production-scale, swap
    this for a filtered pre-query (same city OR shared suspect OR within
    90 days) before building features.
    """
    focal = (
        db.query(Case)
        .options(
            joinedload(Case.suspects).joinedload(CaseSuspect.person),
            joinedload(Case.victims).joinedload(CaseVictim.person),
            joinedload(Case.location),
            joinedload(Case.weapon),
            joinedload(Case.murder_details),
        )
        .filter(Case.id == focal_case_id, Case.is_deleted == False)  # noqa: E712
        .first()
    )
    if not focal:
        return []

    # Pre-filter the candidate pool. We don't need to score against EVERY
    # case in the system — only those plausibly related. This is a cheap
    # SQL filter; the expensive scoring runs only on the survivors.
    suspect_pids = {s.person_id for s in (focal.suspects or []) if s.person_id}
    victim_pids  = {v.person_id for v in (focal.victims  or []) if v.person_id}

    candidate_q = (
        db.query(Case)
        .options(
            joinedload(Case.suspects).joinedload(CaseSuspect.person),
            joinedload(Case.victims).joinedload(CaseVictim.person),
            joinedload(Case.location),
            joinedload(Case.weapon),
            joinedload(Case.murder_details),
        )
        .filter(
            Case.id != focal.id,
            Case.is_deleted == False,  # noqa: E712
        )
    )

    # Recency + locality filter to keep the candidate pool manageable.
    # We accept any case that's either in the same city OR within 180 days
    # OR shares a suspect/victim. This is generous on purpose — false
    # positives at this stage are cheap (we score them out); false
    # negatives are unrecoverable.
    filters = []
    if focal.location and focal.location.city_id:
        filters.append(Case.location.has(city_id=focal.location.city_id))
    if focal.incident_date:
        window_start = focal.incident_date - timedelta(days=180)
        window_end   = focal.incident_date + timedelta(days=180)
        filters.append(Case.incident_date.between(window_start, window_end))
    if filters:
        candidate_q = candidate_q.filter(or_(*filters))

    candidates = candidate_q.limit(candidate_limit).all()

    # Always include cases that share an entity, even if they fell outside
    # the locality/recency window — shared suspect is the strongest signal
    # and we don't want to miss it.
    if suspect_pids or victim_pids:
        all_pids = suspect_pids | victim_pids
        extra_q = (
            db.query(Case)
            .options(
                joinedload(Case.suspects).joinedload(CaseSuspect.person),
                joinedload(Case.victims).joinedload(CaseVictim.person),
                joinedload(Case.location),
                joinedload(Case.weapon),
                joinedload(Case.murder_details),
            )
            .filter(
                Case.id != focal.id,
                Case.is_deleted == False,  # noqa: E712
                or_(
                    Case.suspects.any(CaseSuspect.person_id.in_(all_pids)),
                    Case.victims.any(CaseVictim.person_id.in_(all_pids)),
                ),
            )
        )
        extra = extra_q.all()
        seen = {c.id for c in candidates}
        candidates.extend(c for c in extra if c.id not in seen)

    if not candidates:
        return []

    # Build features once for each case (focal + candidates).
    focal_features = _build_features(db, focal)
    cand_features  = [_build_features(db, c) for c in candidates]

    # Build the graph and pull edges incident to the focal node.
    G = _build_graph([focal_features] + cand_features)

    proposals: List[LinkProposal] = []
    if focal_features.case_id_internal not in G:
        return proposals

    for _, neighbor, data in G.edges(focal_features.case_id_internal, data=True):
        primary = data["primary"]
        if not primary:
            continue
        link_type = LINK_TYPE_CODES.get(primary, "OTHER")
        proposals.append(LinkProposal(
            source_case_id=focal_features.case_id_internal,
            target_case_id=neighbor,
            link_type=link_type,
            confidence=round(data["score"], 4),
            explanation="; ".join(data["reasons"])[:1000],
            shared_entities=data["shared"],
        ))

    return proposals


def compute_case_graph_metrics(db: Session) -> Dict[str, dict]:
    """Build the full case graph and return centrality metrics.
    Useful for the analytics dashboard ("most-connected cases").
    Not called from the after-commit hook — separate analytics endpoint."""
    cases = (
        db.query(Case)
        .options(
            joinedload(Case.suspects).joinedload(CaseSuspect.person),
            joinedload(Case.victims).joinedload(CaseVictim.person),
            joinedload(Case.location),
            joinedload(Case.weapon),
            joinedload(Case.murder_details),
        )
        .filter(Case.is_deleted == False)  # noqa: E712
        .all()
    )
    features = [_build_features(db, c) for c in cases]
    G = _build_graph(features)

    if G.number_of_edges() == 0:
        return {}

    degree = dict(G.degree())
    betweenness = nx.betweenness_centrality(G) if G.number_of_nodes() < 500 else {}
    communities = list(nx.community.greedy_modularity_communities(G)) if G.number_of_edges() > 0 else []

    out: Dict[str, dict] = {}
    for f in features:
        cid = f.case_id_internal
        if cid not in G:
            continue
        community_id = next(
            (i for i, comm in enumerate(communities) if cid in comm), None
        )
        out[f.case_id_external] = {
            "degree": degree.get(cid, 0),
            "betweenness": betweenness.get(cid, 0.0),
            "community": community_id,
        }
    return out