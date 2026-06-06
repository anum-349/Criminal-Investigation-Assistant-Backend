from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import and_, desc, or_
from sqlalchemy.orm import Session, joinedload

from models import (
    Case, CaseLink, CaseSuspect, CaseVictim, CaseWitness,
    CompletenessInconsistency, CompletenessMissingField, CompletenessReport,
    Evidence, ExtractedEntity, InvestigationNote, Lead, Location,
    MurderDetails, SexualAssaultDetails, TheftDetails,
    TimelineEvent, User,
    VictimForensicFinding, VictimLegalMilestone, VictimTimelineEntry,
)
from services.common.service_helper import _resolve_case

log = logging.getLogger(__name__)

MAX_LINKED_CASES = 5

MAX_TIMELINE_EVENTS = 15

SPATIAL_RADIUS_KM = 3.0
SPATIAL_RECENT_DAYS = 30
SPATIAL_MAX_RESULTS = 20

STALE_THRESHOLD_DAYS = 14

MAX_ENTITIES = 25

MAX_NOTES = 10

DETENTION_FLAG_DAYS = 15
EARTH_RADIUS_KM = 6371.0

@dataclass
class SuspectCtx:
    """One suspect, flattened with every field a lead might cite.

    Field notes:
      • days_detained — None unless status looks like Detained/Custody.
        Computed from the latest SUSPECT_ADDED-or-similar timeline event;
        approximated where exact arrest date isn't recorded.
      • has_alibi — true if the alibi field is non-empty AND non-trivial
        (>5 chars). Distinguishes "no alibi recorded" from "alibi: 'no'".
      • is_arrested — Boolean from the model column."""

    suspect_id: str
    name: Optional[str]
    cnic: Optional[str]
    age: Optional[int]
    gender: Optional[str]
    contact: Optional[str]
    address: Optional[str]
    relation: Optional[str]
    status: Optional[str]
    reason: Optional[str]
    alibi: Optional[str]
    has_alibi: bool
    is_arrested: bool
    criminal_record: bool
    affiliations: Optional[str]
    vehicle: Optional[str]
    physical_description: Optional[str]
    notes: Optional[str]
    days_since_added: Optional[int]
    days_detained: Optional[int]            # only set when detained
    flags: List[str] = field(default_factory=list)

@dataclass
class VictimCtx:
    victim_id: str
    name: Optional[str]
    cnic: Optional[str]
    age: Optional[int]
    gender: Optional[str]
    contact: Optional[str]
    status: Optional[str]                   # "Alive" / "Hospitalized" / "Deceased" / …
    primary_label: Optional[str]
    injury_type: Optional[str]
    nature_of_injuries: Optional[str]
    cause_of_death: Optional[str]
    declared_dead: Optional[str]
    postmortem_done: Optional[bool]
    medical_report: Optional[bool]
    statement: Optional[str]
    threat_level: Optional[str]
    protection_required: Optional[bool]
    protection_assigned: Optional[str]
    next_follow_up: Optional[date]
    days_until_follow_up: Optional[int]     # negative ⇒ overdue
    cooperative: Optional[bool]
    flags: List[str] = field(default_factory=list)


@dataclass
class WitnessCtx:
    witness_id: str
    name: Optional[str]                     # None if anonymous
    cnic: Optional[str]                     # None if anonymous
    age: Optional[int]
    contact: Optional[str]
    relation: Optional[str]
    credibility: Optional[str]
    witness_type: Optional[str]
    statement: Optional[str]
    statement_date: Optional[date]
    days_since_statement: Optional[int]
    is_anonymous: bool
    protection_required: bool
    cooperating: bool
    flags: List[str] = field(default_factory=list)


@dataclass
class EvidenceCtx:
    evidence_id: str
    type_label: str
    description: Optional[str]
    date_collected: Optional[date]
    collected_by: Optional[str]
    is_analyzed: bool                       # True if sha256 OR photos exist
    has_file: bool                          # True if file_path is set
    photo_count: int
    days_since_collected: Optional[int]
    flags: List[str] = field(default_factory=list)


@dataclass
class ForensicFindingCtx:
    """One row from victim_forensic_findings. The system DOES store
    forensic findings as free-text on a victim — usually a paragraph
    like 'Toxicology negative for opiates; ligature marks present
    on wrists.' The LLM can cite these by victim_id + finding index."""
    finding_id: str                         # synthetic "FF-{victim_id}-{n}"
    victim_id: str
    finding_text: str
    recorded_at: Optional[datetime]
    days_since_recorded: Optional[int]


@dataclass
class StatementCtx:
    """One statement, normalised so the LLM can compare them in one
    pass. `source_id` references back to a SUS/VIC/WIT id the validator
    already knows."""
    source_kind: str                        # "SUSPECT_ALIBI" / "VICTIM_STATEMENT" / "WITNESS_STATEMENT"
    source_id: str                          # e.g. "SUS-2053-01"
    source_name: Optional[str]
    text: str


@dataclass
class EntityCtx:
    """One FIR-extracted entity. The `matched` flag is the gap signal —
    matched=False entities are people/vehicles named in the FIR but
    never formalised into a suspect/victim/evidence row."""
    type: str                               # PERSON / LOCATION / VEHICLE / WEAPON / DATE / ORG
    value: str
    confidence: float
    verified: bool                          # investigator confirmed
    flagged_for_review: bool
    matched: bool                           # has matched_person_id


@dataclass
class LinkedCaseCtx:
    case_id: str
    title: Optional[str]
    case_type: Optional[str]
    case_status: Optional[str]
    incident_date: Optional[date]
    similarity_score: float                 # 0.0-1.0
    link_type: str                          # SAME_SUSPECT / SAME_WEAPON / …
    explanation: Optional[str]
    investigator_name: str
    shared_suspect_cnics: List[str] = field(default_factory=list)

@dataclass
class NearbyIncidentCtx:
    """One spatially-near incident from the Haversine pass."""
    case_id: str
    title: Optional[str]
    crime_type: Optional[str]
    case_status: Optional[str]
    incident_date: Optional[date]
    days_ago: Optional[int]
    distance_km: float
    priority: Optional[str]


@dataclass
class NearbyDensityCtx:
    """Aggregate over the nearby incidents — what makes a 'hotspot'
    claim grounded. The LLM can cite the count, radius, and period."""
    radius_km: float
    period_days: int
    incident_count: int
    same_crime_type_count: int              # how many were the same crime type
    by_crime_type: Dict[str, int] = field(default_factory=dict)
    case_ids_in_window: List[str] = field(default_factory=list)


@dataclass
class TimelineCtx:
    event_id: str
    event_date: Optional[date]
    event_time: Optional[str]
    event_type: Optional[str]               # the label e.g. "INTERVIEW_CONDUCTED"
    title: str
    description: Optional[str]
    officer_name: Optional[str]
    severity: Optional[str]
    outcome: Optional[str]


@dataclass
class NoteCtx:
    note_id: str
    category: Optional[str]
    officer_name: Optional[str]
    note_date: Optional[date]
    title: str
    detail: str
    days_since_recorded: Optional[int]


@dataclass
class CompletenessCtx:
    """Output of the completeness engine. Already in the DB; we just
    surface it to the LLM so it doesn't suggest leads that the system
    already flagged as needing attention."""
    score: int                              # 0-100
    last_run_at: Optional[datetime]
    missing_fields: List[str] = field(default_factory=list)
    inconsistencies: List[str] = field(default_factory=list)


@dataclass
class GapCtx:
    """A derived inconsistency that the LLM can convert directly into
    a lead. Each gap is a code + human-readable explanation. The validator
    doesn't gate these — they're system-generated, not LLM-cited.

    Codes:
      CCTV_FLAG_NO_EVIDENCE        case.cctv flag set, no CCTV evidence row
      WITNESS_FLAG_NO_RECORD       case.witness_available, no witness rows
      DECEASED_NO_POSTMORTEM       victim deceased, postmortem_done=False
      DECEASED_NO_MURDER_DETAILS   victim deceased but case_type isn't murder
      CRITICAL_NO_SUSPECT          priority=Critical, zero suspects
      OPEN_NO_RECENT_ACTIVITY      open case, no timeline event in 14 days
      SUSPECT_NO_ALIBI             suspect with no alibi recorded
      RECORD_NOT_ARRESTED          suspect with criminal record not arrested
      DETAINED_BEYOND_LIMIT        detention > 15 days
      OVERDUE_FOLLOW_UP            victim follow_up date in the past
      LOW_CRED_AS_PRIMARY          only witness is low-credibility
      ENTITY_PERSON_UNMATCHED      FIR named a person, not in suspects/victims
      ENTITY_VEHICLE_UNMATCHED     FIR named a vehicle, not in evidence/suspect.vehicle
      THEFT_NO_RECOVERY_STATUS     theft case, recovery_status not set
      SA_NO_MEDICAL_EXAM           sexual assault case, no medical exam recorded
      SA_NO_PROTECTION_ORDER       sexual assault case, no protection order"""
    code: str
    human: str
    refs: List[str] = field(default_factory=list)    # related identifier(s) — sus/vic/wit/case/evd

@dataclass
class CaseContext:
    """Everything the prompt sees and everything the validator checks
    against. Treat as immutable after build_case_context returns."""

    # A. CORE
    case_id: str
    case_internal_id: int
    case_title: Optional[str]
    case_type: Optional[str]
    case_status: Optional[str]
    priority: Optional[str]
    fir_number: Optional[str]
    incident_date: Optional[date]
    incident_time: Optional[str]
    reporting_date: Optional[date]
    description: Optional[str]
    motive: Optional[str]
    modus: Optional[str]
    weapon_used: Optional[str]
    weapon_description: Optional[str]
    vehicle_used: Optional[str]
    ppc_sections: Optional[str]
    reporting_officer: Optional[str]
    assigned_investigator: Optional[str]
    cctv_flag: bool
    witness_flag: bool
    num_suspects_expected: Optional[int]

    # Location
    province: Optional[str]
    city: Optional[str]
    area: Optional[str]
    police_station: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]
    crime_scene_type: Optional[str]
    address: Optional[str]

    # Crime-type subtype fields
    cause_of_death: Optional[str] = None
    body_location: Optional[str] = None
    time_of_death: Optional[str] = None
    postmortem_done_subtype: Optional[bool] = None
    forensic_done_subtype: Optional[bool] = None
    medical_exam: Optional[str] = None
    victim_counseling: Optional[bool] = None
    protection_order: Optional[bool] = None
    stolen_items: Optional[str] = None
    stolen_value: Optional[float] = None
    recovery_status: Optional[str] = None
    entry_point: Optional[str] = None

    # B. PEOPLE
    suspects:  List[SuspectCtx] = field(default_factory=list)
    victims:   List[VictimCtx]  = field(default_factory=list)
    witnesses: List[WitnessCtx] = field(default_factory=list)

    # C. STATEMENTS (flattened so the LLM can compare in one pass)
    statements: List[StatementCtx] = field(default_factory=list)

    # D. FORENSIC
    forensic_findings: List[ForensicFindingCtx] = field(default_factory=list)
    evidence: List[EvidenceCtx] = field(default_factory=list)
    evidence_by_type: Dict[str, int] = field(default_factory=dict)
    evidence_analyzed_count: int = 0
    evidence_pending_count: int = 0

    # E. GAPS (derived inconsistencies — system-generated, not LLM-cited)
    gaps: List[GapCtx] = field(default_factory=list)

    # F. ENTITIES from FIR NER
    entities: List[EntityCtx] = field(default_factory=list)

    # G. LINKED CASES
    linked: List[LinkedCaseCtx] = field(default_factory=list)

    # H. NEARBY INCIDENTS (spatial density)
    nearby: List[NearbyIncidentCtx] = field(default_factory=list)
    nearby_density: Optional[NearbyDensityCtx] = None

    # I. TIMELINE
    timeline: List[TimelineCtx] = field(default_factory=list)

    # J. STALENESS / DERIVED COUNTERS
    days_since_incident: Optional[int] = None
    days_since_registered: Optional[int] = None
    days_since_last_timeline_event: Optional[int] = None
    is_stale: bool = False

    # K. NOTES
    notes: List[NoteCtx] = field(default_factory=list)

    # L. COMPLETENESS
    completeness: Optional[CompletenessCtx] = None

    existing_leads: List[str] = field(default_factory=list)  # short descriptions of existing leads, for LLM to avoid duplicates

    # ── Validation indices (pre-built for O(1) reject in validate.py) ──
    all_cnics_seen:    Set[str] = field(default_factory=set)
    all_case_ids_seen: Set[str] = field(default_factory=set)
    all_suspect_ids:   Set[str] = field(default_factory=set)
    all_victim_ids:    Set[str] = field(default_factory=set)
    all_witness_ids:   Set[str] = field(default_factory=set)
    all_evidence_ids:  Set[str] = field(default_factory=set)
    all_finding_ids:   Set[str] = field(default_factory=set)
    all_note_ids:      Set[str] = field(default_factory=set)
    linked_case_ids:   Set[str] = field(default_factory=set)

def _days_between(earlier: Optional[date], later: Optional[date] = None) -> Optional[int]:
    """Whole days between two dates. `later` defaults to today.
    Returns None if `earlier` is missing — never crashes."""
    if not earlier:
        return None
    if later is None:
        later = date.today()
    if isinstance(earlier, datetime):
        earlier = earlier.date()
    if isinstance(later, datetime):
        later = later.date()
    try:
        return (later - earlier).days
    except Exception:
        return None


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in km. Lifted from case_location_service so
    we stay consistent with what the Location tab computes."""
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlng / 2) ** 2)
    return EARTH_RADIUS_KM * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _bbox(lat: float, lng: float, radius_km: float) -> Tuple[float, float, float, float]:
    dlat = radius_km / 111.32
    cos_lat = max(0.000001, math.cos(math.radians(lat)))
    dlng = radius_km / (111.32 * cos_lat)
    return (lat - dlat, lat + dlat, lng - dlng, lng + dlng)


def _has_meaningful_text(s: Optional[str], min_chars: int = 5) -> bool:
    """Distinguish 'no alibi recorded' from 'alibi: -'."""
    if not s:
        return False
    cleaned = s.strip().strip("-.").strip()
    return len(cleaned) >= min_chars

def build_case_context(
    db: Session,
    *,
    user: User,
    case_id: str,
) -> CaseContext:
    """Pull every aspect of the case in the fewest queries we can manage
    without making the eager-load chain unreadable. Roughly 4 queries:
        1. Resolve + load full case w/ joinedload tree
        2. Forensic findings  (separate table per-victim aggregation)
        3. Nearby incidents   (spatial bbox + haversine)
        4. Investigation notes + completeness report

    Total time for a typical case: ~50ms (matches design § 8 budget).
    """
    case = _resolve_case(db, user=user, case_id=case_id)

    full = (
        db.query(Case)
        .options(
            joinedload(Case.case_type),
            joinedload(Case.case_status),
            joinedload(Case.priority),
            joinedload(Case.location).joinedload(Location.province),
            joinedload(Case.location).joinedload(Location.city),
            joinedload(Case.weapon),
            joinedload(Case.assigned_to),
            joinedload(Case.suspects).joinedload(CaseSuspect.person),
            joinedload(Case.suspects).joinedload(CaseSuspect.status),
            joinedload(Case.victims).joinedload(CaseVictim.person),
            joinedload(Case.victims).joinedload(CaseVictim.status),
            joinedload(Case.victims).joinedload(CaseVictim.threat_level),
            joinedload(Case.witnesses).joinedload(CaseWitness.person),
            joinedload(Case.witnesses).joinedload(CaseWitness.credibility),
            joinedload(Case.witnesses).joinedload(CaseWitness.witness_type),
            joinedload(Case.evidences).joinedload(Evidence.type),
            joinedload(Case.evidences).joinedload(Evidence.photos),
            joinedload(Case.extracted_entities),
            joinedload(Case.murder_details),
            joinedload(Case.completeness_report).joinedload(
                CompletenessReport.missing_fields
            ),
            joinedload(Case.completeness_report).joinedload(
                CompletenessReport.inconsistencies
            ),
        )
        .filter(Case.id == case.id)
        .first()
    )
    if not full:
        raise RuntimeError(f"Lost case {case_id} between resolve and load")

    loc = full.location

    # ── A. Build core header ──────────────────────────────────────
    assigned_name = None
    if full.assigned_to and full.assigned_to.user:
        rank = (full.assigned_to.rank or "").strip()
        uname = full.assigned_to.user.username
        assigned_name = f"{rank}. {uname}" if rank else uname

    ctx = CaseContext(
        case_id          = full.case_id,
        case_internal_id = full.id,
        case_title       = full.case_title,
        case_type        = full.case_type.label if full.case_type else None,
        case_status      = full.case_status.label if full.case_status else None,
        priority         = full.priority.label if full.priority else None,
        fir_number       = full.fir_number,
        incident_date    = full.incident_date,
        incident_time    = full.incident_time,
        reporting_date   = full.reporting_date,
        description      = full.description,
        motive           = getattr(full, "motive", None),
        modus            = getattr(full, "modus_operandi", None) or getattr(full, "modus", None),
        weapon_used      = full.weapon.label if full.weapon else None,
        weapon_description = getattr(full, "weapon_description", None),
        vehicle_used     = getattr(full, "vehicle_used", None),
        ppc_sections     = full.ppc_sections,
        reporting_officer= full.reporting_officer,
        assigned_investigator = assigned_name,
        cctv_flag        = bool(getattr(full, "cctv_available", False)),
        witness_flag     = bool(getattr(full, "witness_available", False)),
        num_suspects_expected = getattr(full, "num_suspects", None),
        province         = loc.province.label if (loc and loc.province) else None,
        city             = loc.city.name if (loc and loc.city) else None,
        area             = loc.area if loc else None,
        police_station   = loc.police_station if loc else None,
        latitude         = loc.latitude if loc else None,
        longitude        = loc.longitude if loc else None,
        crime_scene_type = getattr(loc, "crime_scene_type", None) if loc else None,
        address          = (loc.full_address or loc.display_address) if loc else None,
    )
    ctx.all_case_ids_seen.add(full.case_id)

    # Derived staleness counters
    ctx.days_since_incident   = _days_between(full.incident_date)
    ctx.days_since_registered = _days_between(full.created_at.date() if full.created_at else None)

    # ── Crime-type subtype extras ─────────────────────────────────
    if full.murder_details:
        ctx.cause_of_death          = full.murder_details.cause_of_death
        ctx.body_location           = full.murder_details.body_location
        ctx.time_of_death           = full.murder_details.time_of_death
        ctx.postmortem_done_subtype = bool(full.murder_details.postmortem_done)
        ctx.forensic_done_subtype   = bool(full.murder_details.forensic_done)

    sa = db.query(SexualAssaultDetails).filter(
        SexualAssaultDetails.case_id_fk == full.id
    ).first()
    if sa:
        ctx.medical_exam       = sa.medical_exam
        ctx.victim_counseling  = bool(sa.victim_counseling)
        ctx.protection_order   = bool(sa.protection_order)

    th = db.query(TheftDetails).filter(
        TheftDetails.case_id_fk == full.id
    ).first()
    if th:
        ctx.stolen_items     = th.stolen_items
        ctx.stolen_value     = th.stolen_value
        ctx.recovery_status  = th.recovery_status
        ctx.entry_point      = th.entry_point

    # ── B. SUSPECTS ────────────────────────────────────────────────
    for s in (full.suspects or []):
        sus_id = s.suspect_id or f"SUS-{full.case_id}-{s.id:02d}"
        person = s.person
        cnic   = person.cnic if person else None
        has_alibi = _has_meaningful_text(s.alibi)
        status_label = s.status.label if s.status else None

        days_detained: Optional[int] = None
        if status_label and "detain" in status_label.lower():
            days_detained = _days_between(
                s.created_at.date() if s.created_at else None
            )

        flags: List[str] = []
        if not has_alibi:
            flags.append("NO_ALIBI")
        if bool(s.criminal_record) and not bool(s.arrested):
            flags.append("CRIMINAL_RECORD_NOT_ARRESTED")
        if days_detained is not None and days_detained > DETENTION_FLAG_DAYS:
            flags.append("DETAINED_BEYOND_LIMIT")

        sus = SuspectCtx(
            suspect_id     = sus_id,
            name           = person.full_name if person else None,
            cnic           = cnic,
            age            = person.age if person else None,
            gender         = person.gender if person else None,
            contact        = person.contact if person else None,
            address        = person.address if person else None,
            relation       = s.relation_to_case,
            status         = status_label,
            reason         = s.reason,
            alibi          = s.alibi if has_alibi else None,
            has_alibi      = has_alibi,
            is_arrested    = bool(s.arrested),
            criminal_record= bool(s.criminal_record),
            affiliations   = s.known_affiliations,
            vehicle        = s.vehicle_description,
            physical_description = person.physical_description if person else None,
            notes          = s.notes,
            days_since_added = _days_between(s.created_at.date() if s.created_at else None),
            days_detained  = days_detained,
            flags          = flags,
        )
        ctx.suspects.append(sus)
        ctx.all_suspect_ids.add(sus_id)
        if cnic:
            ctx.all_cnics_seen.add(cnic)

        if has_alibi:
            ctx.statements.append(StatementCtx(
                source_kind = "SUSPECT_ALIBI",
                source_id   = sus_id,
                source_name = sus.name,
                text        = s.alibi.strip(),
            ))

    deceased_victim_present = False
    for v in (full.victims or []):
        vic_id = v.victim_id or f"VIC-{full.case_id}-{v.id:02d}"
        person = v.person
        cnic   = person.cnic if person else None
        status_label = v.status.label if v.status else None
        if status_label and "decease" in status_label.lower():
            deceased_victim_present = True

        flags: List[str] = []
        if (status_label or "").lower() in ("deceased", "dead"):
            if not bool(v.postmortem):
                flags.append("DECEASED_NO_POSTMORTEM")
            if not full.murder_details and (full.case_type and "murder" not in full.case_type.label.lower()):
                flags.append("DECEASED_NO_MURDER_DETAILS")

        days_until = None
        if v.next_follow_up:
            days_until = _days_between(date.today(), v.next_follow_up)
            if days_until is not None and days_until < 0:
                flags.append("OVERDUE_FOLLOW_UP")

        if bool(v.protection_required) and not (v.protection_assigned or "").strip():
            flags.append("PROTECTION_NEEDED_NOT_ASSIGNED")

        vctx = VictimCtx(
            victim_id          = vic_id,
            name               = person.full_name if person else None,
            cnic               = cnic,
            age                = person.age if person else None,
            gender             = person.gender if person else None,
            contact            = person.contact if person else None,
            status             = status_label,
            primary_label      = v.primary_label,
            injury_type        = v.injury_type,
            nature_of_injuries = v.nature_of_injuries,
            cause_of_death     = v.cause_of_death,
            declared_dead      = v.declared_dead,
            postmortem_done    = bool(v.postmortem),
            medical_report     = bool(v.medical_report),
            statement          = v.statement if _has_meaningful_text(v.statement) else None,
            threat_level       = v.threat_level.label if v.threat_level else None,
            protection_required= bool(v.protection_required),
            protection_assigned= v.protection_assigned,
            next_follow_up     = v.next_follow_up,
            days_until_follow_up = days_until,
            cooperative        = bool(v.cooperative),
            flags              = flags,
        )
        ctx.victims.append(vctx)
        ctx.all_victim_ids.add(vic_id)
        if cnic:
            ctx.all_cnics_seen.add(cnic)

        if vctx.statement:
            ctx.statements.append(StatementCtx(
                source_kind = "VICTIM_STATEMENT",
                source_id   = vic_id,
                source_name = vctx.name,
                text        = vctx.statement,
            ))

        for idx, ff in enumerate(v.forensic_findings or [], start=1):
            finding_id = f"FF-{vic_id}-{idx:02d}"
            ctx.forensic_findings.append(ForensicFindingCtx(
                finding_id   = finding_id,
                victim_id    = vic_id,
                finding_text = ff.finding_text,
                recorded_at  = ff.recorded_at,
                days_since_recorded = _days_between(
                    ff.recorded_at.date() if ff.recorded_at else None
                ),
            ))
            ctx.all_finding_ids.add(finding_id)

    # ── B. WITNESSES ───────────────────────────────────────────────
    for w in (full.witnesses or []):
        wit_id = w.witness_id or f"WIT-{full.case_id}-{w.id:02d}"
        person = w.person
        is_anon = bool(getattr(w, "anonymous", False))
        cnic = None if is_anon else (person.cnic if person else None)
        credibility_label = w.credibility.label if w.credibility else None

        flags: List[str] = []
        if credibility_label and credibility_label.lower() in ("low", "contradicted", "unverified"):
            flags.append("LOW_CREDIBILITY")
        if bool(w.protection_required):
            flags.append("PROTECTION_REQUIRED")
        if not bool(getattr(w, "cooperating", True)):
            flags.append("NOT_COOPERATING")

        wctx = WitnessCtx(
            witness_id   = wit_id,
            name         = None if is_anon else (person.full_name if person else None),
            cnic         = cnic,
            age          = person.age if (person and not is_anon) else None,
            contact      = None if is_anon else (person.contact if person else None),
            relation     = w.relation_to_case,
            credibility  = credibility_label,
            witness_type = w.witness_type.label if w.witness_type else None,
            statement    = w.description if _has_meaningful_text(w.description) else None,
            statement_date = w.statement_date,
            days_since_statement = _days_between(w.statement_date),
            is_anonymous = is_anon,
            protection_required = bool(w.protection_required),
            cooperating  = bool(getattr(w, "cooperating", True)),
            flags        = flags,
        )
        ctx.witnesses.append(wctx)
        ctx.all_witness_ids.add(wit_id)
        if cnic:
            ctx.all_cnics_seen.add(cnic)

        if wctx.statement:
            ctx.statements.append(StatementCtx(
                source_kind = "WITNESS_STATEMENT",
                source_id   = wit_id,
                source_name = wctx.name,
                text        = wctx.statement,
            ))

    # ── D. EVIDENCE ────────────────────────────────────────────────
    cctv_evidence_present = False
    for e in (full.evidences or []):
        ev_id = e.evidence_id
        type_label = e.type.label if e.type else "Other"
        is_analyzed = bool(e.sha256_hash) or bool(e.photos)
        if is_analyzed:
            ctx.evidence_analyzed_count += 1
        else:
            ctx.evidence_pending_count += 1
        ctx.evidence_by_type[type_label] = ctx.evidence_by_type.get(type_label, 0) + 1

        if "cctv" in type_label.lower() or "video" in type_label.lower():
            cctv_evidence_present = True

        flags: List[str] = []
        if not is_analyzed:
            flags.append("PENDING_ANALYSIS")

        days_collected = _days_between(e.date_collected)
        if days_collected is not None and days_collected > 7 and not is_analyzed:
            flags.append("STALE_PENDING")

        ctx.evidence.append(EvidenceCtx(
            evidence_id   = ev_id,
            type_label    = type_label,
            description   = e.description,
            date_collected= e.date_collected,
            collected_by  = e.collected_by,
            is_analyzed   = is_analyzed,
            has_file      = bool(e.file_path),
            photo_count   = len(e.photos or []),
            days_since_collected = days_collected,
            flags         = flags,
        ))
        ctx.all_evidence_ids.add(ev_id)

    # ── F. ENTITIES extracted from FIR ─────────────────────────────
    fir_persons_unmatched: List[str] = []
    fir_vehicles_unmatched: List[str] = []
    for ent in (full.extracted_entities or [])[:MAX_ENTITIES]:
        if not ent.entity_value:
            continue
        is_matched = bool(ent.matched_person_id)
        ctx.entities.append(EntityCtx(
            type       = (ent.entity_type or "OTHER").upper(),
            value      = ent.user_corrected_value or ent.entity_value,
            confidence = float(ent.confidence or 0.0),
            verified   = bool(ent.verified_by_user),
            flagged_for_review = bool(ent.flagged_for_review),
            matched    = is_matched,
        ))
        if not is_matched:
            t = (ent.entity_type or "").upper()
            if t == "PERSON":
                fir_persons_unmatched.append(ent.entity_value)
            elif t == "VEHICLE":
                fir_vehicles_unmatched.append(ent.entity_value)

    # ── G. LINKED CASES ────────────────────────────────────────────
    links = (
        db.query(CaseLink)
        .filter(CaseLink.source_case_id == full.id)
        .order_by(desc(CaseLink.similarity_score))
        .limit(MAX_LINKED_CASES)
        .all()
    )
    focal_suspect_cnics = {s.cnic for s in ctx.suspects if s.cnic}
    for link in links:
        other = (
            db.query(Case)
            .options(
                joinedload(Case.case_type),
                joinedload(Case.case_status),
                joinedload(Case.assigned_to),
                joinedload(Case.suspects).joinedload(CaseSuspect.person),
            )
            .filter(Case.id == link.target_case_id)
            .first()
        )
        if not other or other.is_deleted:
            continue
        inv_name = "Unknown"
        if other.assigned_to and other.assigned_to.user:
            rank = (other.assigned_to.rank or "").strip()
            inv_name = f"{rank}. {other.assigned_to.user.username}" if rank else other.assigned_to.user.username

        # If we share a suspect, mark which CNIC for cross-case lead.
        shared_cnics: List[str] = []
        for s in (other.suspects or []):
            if s.person and s.person.cnic and s.person.cnic in focal_suspect_cnics:
                shared_cnics.append(s.person.cnic)

        ctx.linked.append(LinkedCaseCtx(
            case_id          = other.case_id,
            title            = other.case_title,
            case_type        = other.case_type.label if other.case_type else None,
            case_status      = other.case_status.label if other.case_status else None,
            incident_date    = other.incident_date,
            similarity_score = float(link.similarity_score or 0.0),
            link_type        = link.link_type,
            explanation      = link.explanation,
            investigator_name= inv_name,
            shared_suspect_cnics = shared_cnics,
        ))
        ctx.linked_case_ids.add(other.case_id)
        ctx.all_case_ids_seen.add(other.case_id)

    if ctx.latitude and ctx.longitude:
        lat_min, lat_max, lng_min, lng_max = _bbox(ctx.latitude, ctx.longitude, SPATIAL_RADIUS_KM)
        cutoff_date = date.today() - timedelta(days=SPATIAL_RECENT_DAYS)
        nearby_q = (
            db.query(Case)
            .join(Location, Location.case_id_fk == Case.id)
            .filter(
                Case.is_deleted == False,                       # noqa: E712
                Case.id != full.id,
                Case.incident_date >= cutoff_date,
                Location.latitude.isnot(None),
                Location.longitude.isnot(None),
                Location.latitude.between(lat_min, lat_max),
                Location.longitude.between(lng_min, lng_max),
            )
            .options(
                joinedload(Case.case_type),
                joinedload(Case.case_status),
                joinedload(Case.priority),
                joinedload(Case.location),
            )
        )
        candidates = nearby_q.all()
        by_type: Dict[str, int] = {}
        same_type_count = 0
        case_ids_window: List[str] = []
        for c in candidates:
            l = c.location
            if not l or l.latitude is None or l.longitude is None:
                continue
            d_km = _haversine_km(ctx.latitude, ctx.longitude, l.latitude, l.longitude)
            if d_km > SPATIAL_RADIUS_KM:
                continue
            crime_t = c.case_type.label if c.case_type else "Unknown"
            by_type[crime_t] = by_type.get(crime_t, 0) + 1
            if crime_t == ctx.case_type:
                same_type_count += 1
            case_ids_window.append(c.case_id)
            ctx.all_case_ids_seen.add(c.case_id)
            ctx.nearby.append(NearbyIncidentCtx(
                case_id       = c.case_id,
                title         = c.case_title,
                crime_type    = crime_t,
                case_status   = c.case_status.label if c.case_status else None,
                incident_date = c.incident_date,
                days_ago      = _days_between(c.incident_date),
                distance_km   = round(d_km, 2),
                priority      = c.priority.label if c.priority else None,
            ))
        ctx.nearby.sort(key=lambda r: r.distance_km)
        ctx.nearby = ctx.nearby[:SPATIAL_MAX_RESULTS]
        ctx.nearby_density = NearbyDensityCtx(
            radius_km             = SPATIAL_RADIUS_KM,
            period_days           = SPATIAL_RECENT_DAYS,
            incident_count        = len(ctx.nearby),
            same_crime_type_count = same_type_count,
            by_crime_type         = by_type,
            case_ids_in_window    = case_ids_window[:SPATIAL_MAX_RESULTS],
        )

    # ── I. TIMELINE (recent events) ────────────────────────────────
    tl_rows = (
        db.query(TimelineEvent)
        .filter(TimelineEvent.case_id_fk == full.id)
        .order_by(desc(TimelineEvent.created_at))
        .options(
            joinedload(TimelineEvent.event_type),
            joinedload(TimelineEvent.severity),
        )
        .limit(MAX_TIMELINE_EVENTS)
        .all()
    )
    if tl_rows:
        # latest one is row[0] — derive staleness
        latest = tl_rows[0]
        ctx.days_since_last_timeline_event = _days_between(
            latest.created_at.date() if latest.created_at else None
        )
        if ctx.days_since_last_timeline_event is not None:
            ctx.is_stale = ctx.days_since_last_timeline_event > STALE_THRESHOLD_DAYS
    # Reverse so prompt sees chronological order
    for t in reversed(tl_rows):
        ctx.timeline.append(TimelineCtx(
            event_id     = t.event_id or f"EVT-{t.id}",
            event_date   = t.event_date,
            event_time   = t.event_time,
            event_type   = t.event_type.label if t.event_type else None,
            title        = t.title or "",
            description  = t.description,
            officer_name = t.officer_name,
            severity     = t.severity.label if t.severity else None,
            outcome      = t.outcome,
        ))

    # ── K. INVESTIGATION NOTES ─────────────────────────────────────
    notes_q = (
        db.query(InvestigationNote)
        .filter(InvestigationNote.case_id_fk == full.id)
        .order_by(desc(InvestigationNote.created_at))
        .options(joinedload(InvestigationNote.category))
        .limit(MAX_NOTES)
        .all()
    )
    for n in notes_q:
        ctx.notes.append(NoteCtx(
            note_id      = n.note_id,
            category     = n.category.label if n.category else None,
            officer_name = n.officer_name,
            note_date    = n.note_date,
            title        = n.title,
            detail       = (n.detail or "")[:400],
            days_since_recorded = _days_between(
                n.created_at.date() if n.created_at else None
            ),
        ))
        ctx.all_note_ids.add(n.note_id)

    # ── L. COMPLETENESS REPORT ────────────────────────────────────
    if full.completeness_report:
        cr = full.completeness_report
        ctx.completeness = CompletenessCtx(
            score          = int(cr.score or 0),
            last_run_at    = cr.last_run_at,
            missing_fields = [m.field_name for m in (cr.missing_fields or [])],
            inconsistencies= [
                f"{ic.field_name}: {ic.problem}"
                for ic in (cr.inconsistencies or [])
            ],
        )

    # ── E. DERIVED GAPS (the lead generator's secret sauce) ────────
    ctx.gaps = _compute_gaps(
        ctx           = ctx,
        cctv_present  = cctv_evidence_present,
        deceased      = deceased_victim_present,
        fir_persons_unmatched  = fir_persons_unmatched,
        fir_vehicles_unmatched = fir_vehicles_unmatched,
    )

    existing = (
        db.query(Lead.description)
        .filter(
            Lead.case_id_fk == full.id,
            Lead.event_source == "AI",
        )
        .order_by(desc(Lead.generated_at))
        .limit(10)
        .all()
    )
    ctx.existing_leads = [
        row.description.split("\n")[0].strip()
        for row in existing
        if row.description
    ]

    return ctx

def _compute_gaps(
    *,
    ctx: CaseContext,
    cctv_present: bool,
    deceased: bool,
    fir_persons_unmatched: List[str],
    fir_vehicles_unmatched: List[str],
) -> List[GapCtx]:
    """Roll up every derived inconsistency into one list. Each entry
    becomes a bullet in the prompt's GAPS section.

    Order matters here — gaps are emitted to the LLM in this order, and
    the LLM tends to lead with the first gap it sees. We put the
    highest-stakes ones first."""
    gaps: List[GapCtx] = []

    # 1. Critical priority with no suspects ─────────────────────────
    if (ctx.priority or "").lower() in ("critical", "high") and not ctx.suspects:
        gaps.append(GapCtx(
            code   = "CRITICAL_NO_SUSPECT",
            human  = (
                f"Case priority is {ctx.priority} but zero suspects are "
                "on record. Identifying any plausible suspect is the "
                "highest-impact next step."
            ),
        ))

    # 2. Deceased victim, missing post-mortem ─────────────────────
    for v in ctx.victims:
        if "DECEASED_NO_POSTMORTEM" in v.flags:
            gaps.append(GapCtx(
                code  = "DECEASED_NO_POSTMORTEM",
                human = (
                    f"Victim {v.victim_id} ({v.name or 'unnamed'}) is "
                    "deceased but post-mortem is not marked complete."
                ),
                refs  = [v.victim_id],
            ))

    # 3. Detained beyond legal limit ───────────────────────────────
    for s in ctx.suspects:
        if "DETAINED_BEYOND_LIMIT" in s.flags:
            gaps.append(GapCtx(
                code  = "DETAINED_BEYOND_LIMIT",
                human = (
                    f"Suspect {s.suspect_id} has been detained "
                    f"{s.days_detained} days. Review remand authorisation."
                ),
                refs  = [s.suspect_id],
            ))

    # 4. Overdue victim follow-up ─────────────────────────────────
    for v in ctx.victims:
        if "OVERDUE_FOLLOW_UP" in v.flags:
            days_overdue = abs(v.days_until_follow_up) if v.days_until_follow_up else 0
            gaps.append(GapCtx(
                code  = "OVERDUE_FOLLOW_UP",
                human = (
                    f"Follow-up for victim {v.victim_id} is overdue "
                    f"by {days_overdue} day(s)."
                ),
                refs  = [v.victim_id],
            ))

    # 5. Witness/CCTV flag without record ─────────────────────────
    if ctx.cctv_flag and not cctv_present:
        gaps.append(GapCtx(
            code  = "CCTV_FLAG_NO_EVIDENCE",
            human = (
                "Case file indicates CCTV was available, but no CCTV/video "
                "evidence has been collected."
            ),
        ))
    if ctx.witness_flag and not ctx.witnesses:
        gaps.append(GapCtx(
            code  = "WITNESS_FLAG_NO_RECORD",
            human = (
                "Case file indicates witnesses were available, but no "
                "witness records exist on file."
            ),
        ))

    # 6. Suspects with criminal record not arrested ──────────────
    for s in ctx.suspects:
        if "CRIMINAL_RECORD_NOT_ARRESTED" in s.flags:
            gaps.append(GapCtx(
                code  = "RECORD_NOT_ARRESTED",
                human = (
                    f"Suspect {s.suspect_id} ({s.name or 'unnamed'}) has a "
                    "prior criminal record and is not arrested."
                ),
                refs  = [s.suspect_id],
            ))

    # 7. Suspects with no alibi recorded ─────────────────────────
    no_alibi = [s for s in ctx.suspects if "NO_ALIBI" in s.flags]
    if no_alibi:
        # Roll up into one gap so it doesn't dominate the prompt
        ids = [s.suspect_id for s in no_alibi]
        gaps.append(GapCtx(
            code  = "SUSPECT_NO_ALIBI",
            human = (
                f"{len(no_alibi)} suspect(s) have no alibi on record: "
                f"{', '.join(ids)}."
            ),
            refs  = ids,
        ))

    # 8. Stale case ─────────────────────────────────────────────
    if ctx.is_stale and (ctx.case_status or "").lower() not in ("closed", "solved"):
        gaps.append(GapCtx(
            code  = "OPEN_NO_RECENT_ACTIVITY",
            human = (
                f"Case is open and {ctx.days_since_last_timeline_event} "
                "days have passed since the last timeline event."
            ),
        ))

    # 9. FIR named a person never added to records ───────────────
    for name in fir_persons_unmatched[:5]:        # cap to keep prompt tight
        gaps.append(GapCtx(
            code  = "ENTITY_PERSON_UNMATCHED",
            human = (
                f"FIR mentions a person ('{name}') with no matching record "
                "in the case suspects/victims/witnesses tables."
            ),
        ))

    # 10. FIR named a vehicle never traced ────────────────────────
    for plate in fir_vehicles_unmatched[:5]:
        gaps.append(GapCtx(
            code  = "ENTITY_VEHICLE_UNMATCHED",
            human = (
                f"FIR mentions a vehicle ('{plate}') with no matching "
                "evidence record or suspect-vehicle field."
            ),
        ))

    # 11. Theft case without recovery status ─────────────────────
    if ctx.case_type and "theft" in ctx.case_type.lower() and not ctx.recovery_status:
        gaps.append(GapCtx(
            code  = "THEFT_NO_RECOVERY_STATUS",
            human = "Theft case has no recovery status recorded.",
        ))

    # 12. Sexual assault case completeness ───────────────────────
    if ctx.case_type and "sexual" in ctx.case_type.lower():
        if not _has_meaningful_text(ctx.medical_exam):
            gaps.append(GapCtx(
                code  = "SA_NO_MEDICAL_EXAM",
                human = (
                    "Sexual assault case has no medical-exam record. "
                    "Time-sensitive evidence may be lost."
                ),
            ))
        if not ctx.protection_order:
            gaps.append(GapCtx(
                code  = "SA_NO_PROTECTION_ORDER",
                human = (
                    "Sexual assault case has no protection order on file."
                ),
            ))

    # 13. Only-witness is low-credibility ───────────────────────
    if ctx.witnesses and all("LOW_CREDIBILITY" in w.flags for w in ctx.witnesses):
        gaps.append(GapCtx(
            code  = "LOW_CRED_AS_PRIMARY",
            human = (
                "All witnesses on file have low or contradicted "
                "credibility. Identifying a corroborating witness is "
                "critical."
            ),
            refs  = [w.witness_id for w in ctx.witnesses],
        ))

    # 14. Witnesses needing protection but none assigned ───────
    for w in ctx.witnesses:
        if w.protection_required:
            gaps.append(GapCtx(
                code  = "WITNESS_PROTECTION_REQUIRED",
                human = (
                    f"Witness {w.witness_id} has flagged the need for "
                    "protection. Verify assignment status."
                ),
                refs  = [w.witness_id],
            ))

    # 15. Evidence backlog ─────────────────────────────────────
    if ctx.evidence_pending_count >= 3:
        gaps.append(GapCtx(
            code  = "EVIDENCE_BACKLOG",
            human = (
                f"{ctx.evidence_pending_count} evidence items remain "
                "pending analysis."
            ),
        ))

    # 16. Non-cooperating victim/witness ─────────────────────────
    for v in ctx.victims:
        if v.cooperative is False:
            gaps.append(GapCtx(
                code  = "VICTIM_NOT_COOPERATING",
                human = (
                    f"Victim {v.victim_id} is non-cooperative. Consider "
                    "follow-up with victim-support unit."
                ),
                refs  = [v.victim_id],
            ))
    for w in ctx.witnesses:
        if "NOT_COOPERATING" in w.flags:
            gaps.append(GapCtx(
                code  = "WITNESS_NOT_COOPERATING",
                human = (
                    f"Witness {w.witness_id} is non-cooperative."
                ),
                refs  = [w.witness_id],
            ))

    # 17. Cross-case suspect re-interview opportunity ───────────
    for l in ctx.linked:
        if l.shared_suspect_cnics:
            cnic_list = ", ".join(l.shared_suspect_cnics)
            gaps.append(GapCtx(
                code  = "CROSS_CASE_SHARED_SUSPECT",
                human = (
                    f"Suspect (CNIC {cnic_list}) appears in linked case "
                    f"{l.case_id} ({l.title or 'untitled'}). Cross-case "
                    "interview may yield new information."
                ),
                refs  = [l.case_id] + l.shared_suspect_cnics,
            ))

    # 18. High spatial density of same crime type ──────────────
    if ctx.nearby_density and ctx.nearby_density.same_crime_type_count >= 3:
        gaps.append(GapCtx(
            code  = "SPATIAL_PATTERN",
            human = (
                f"{ctx.nearby_density.same_crime_type_count} similar "
                f"{ctx.case_type or 'incident'} cases within "
                f"{ctx.nearby_density.radius_km}km in the last "
                f"{ctx.nearby_density.period_days} days. "
                "Possible pattern crime."
            ),
            refs  = ctx.nearby_density.case_ids_in_window[:5],
        ))

    return gaps


@dataclass
class ContextDepthReport:
    is_deep_enough: bool
    score: int
    reasons: List[str]


def assess_context_depth(ctx: CaseContext) -> ContextDepthReport:
    """Heuristic: skip the LLM call for a context too thin to produce
    grounded leads. A case with only a title and no entities or
    evidence will return noise no matter how good the prompt is."""
    score = 0
    reasons: List[str] = []

    if ctx.suspects:
        score += 3
        reasons.append(f"{len(ctx.suspects)} suspect(s)")
    if ctx.victims:
        score += 1
        reasons.append(f"{len(ctx.victims)} victim(s)")
    if ctx.witnesses:
        score += 1
        reasons.append(f"{len(ctx.witnesses)} witness(es)")
    if ctx.evidence:
        score += 2
        reasons.append(f"{len(ctx.evidence)} evidence item(s)")
    if ctx.forensic_findings:
        score += 1
        reasons.append(f"{len(ctx.forensic_findings)} forensic finding(s)")
    if ctx.entities:
        score += 2
        reasons.append(f"{len(ctx.entities)} extracted entit(y/ies)")
    if ctx.linked:
        score += 2
        reasons.append(f"{len(ctx.linked)} linked case(s)")
    if ctx.nearby:
        score += 1
        reasons.append(f"{len(ctx.nearby)} nearby incident(s)")
    if ctx.gaps:
        score += 2
        reasons.append(f"{len(ctx.gaps)} derived gap(s)")
    if ctx.description and len(ctx.description) >= 100:
        score += 1
        reasons.append("substantial case description")
    if ctx.notes:
        score += 1
        reasons.append(f"{len(ctx.notes)} investigator note(s)")

    is_deep = score >= 3
    if not is_deep:
        reasons.insert(0, "Insufficient grounded data for meaningful leads")
    return ContextDepthReport(is_deep_enough=is_deep, score=score, reasons=reasons)
