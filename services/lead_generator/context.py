"""
services/lead_generator/context.py
─────────────────────────────────────────────────────────────────────────────
Phase 2 of the lead generator: pull everything the LLM needs to know
about a case from the DB, plus a flat index of every identifier we saw
(CNICs, case IDs, suspect IDs) so the validation layer (§ 6) can reject
anything the model invents.

Why a dataclass rather than just passing the SQLAlchemy rows around:
  • The prompt builder needs a *frozen snapshot* — if we passed live
    ORM objects, lazy-loading inside the prompt template would issue
    surprise SQL queries while we're talking to the LLM.
  • The validator needs O(1) lookups against pre-built sets; computing
    them once here (rather than scanning rows on every check) keeps
    validation snappy.
  • Mocking in tests becomes trivial — build a CaseContext by hand,
    skip the DB.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional, Set, Tuple

from sqlalchemy import desc
from sqlalchemy.orm import Session, joinedload

from models import (
    Case, CaseSuspect, CaseVictim, CaseWitness,
    Evidence, ExtractedEntity, CaseLink, TimelineEvent,
    MurderDetails, SexualAssaultDetails, TheftDetails,
)
from services.service_helper import _resolve_case
from models import User

log = logging.getLogger(__name__)

# Cap linked cases at 5 — design § 4 step 1. Beyond that, the prompt
# bloats and the LLM starts hallucinating cross-references.
MAX_LINKED_CASES = 5

# Cap timeline events too — most cases have ~3-10 events. We want recent
# context, not the whole history.
MAX_TIMELINE_EVENTS = 15


# ─────────────────────────────────────────────────────────────────────────────
# Lightweight value objects (NOT SQLAlchemy rows — these are detached
# snapshots safe to hold across the LLM call)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SuspectCtx:
    suspect_id: str           # external "SUS-2053-01"
    name: Optional[str]
    cnic: Optional[str]
    age: Optional[int]
    relation: Optional[str]
    criminal_record: bool
    affiliations: Optional[str]
    vehicle: Optional[str]
    reason: Optional[str]


@dataclass
class VictimCtx:
    victim_id: str
    name: Optional[str]
    cnic: Optional[str]
    age: Optional[int]
    status: Optional[str]


@dataclass
class WitnessCtx:
    witness_id: str
    name: Optional[str]        # null if anonymous
    cnic: Optional[str]
    credibility: Optional[str]
    statement: Optional[str]


@dataclass
class EvidenceCtx:
    evidence_id: str
    type_label: str
    description: Optional[str]


@dataclass
class EntityCtx:
    """One row from extracted_entities — a thing the FIR mentioned but
    that wasn't manually entered into the structured suspect/victim
    tables. These are gold for lead generation: they're the gaps the
    LLM should be asking the investigator about."""
    type: str                  # PERSON / LOCATION / VEHICLE / WEAPON / DATE
    value: str
    notes: Optional[str]


@dataclass
class LinkedCaseCtx:
    case_id: str               # external "C-2040"
    title: Optional[str]
    incident_date: Optional[date]
    similarity_score: float    # 0.0–1.0
    link_type: str             # "SAME_SUSPECT" / etc.
    explanation: Optional[str]
    investigator_name: str


@dataclass
class TimelineCtx:
    event_date: Optional[date]
    event_time: Optional[str]
    title: str
    description: Optional[str]


@dataclass
class CaseContext:
    """Everything the prompt sees + everything the validator checks
    against. The pre-indexed sets at the bottom are the validator's
    hot path."""

    # ── Core case fields ────────────────────────────────────────────
    case_id: str                       # external "C-2053"
    case_internal_id: int              # PK, for joins
    case_title: Optional[str]
    case_type: Optional[str]
    case_status: Optional[str]
    fir_number: Optional[str]
    incident_date: Optional[date]
    incident_time: Optional[str]
    description: Optional[str]
    motive: Optional[str]
    modus: Optional[str]
    weapon_used: Optional[str]
    ppc_sections: Optional[str]
    reporting_officer: Optional[str]
    province: Optional[str]
    city: Optional[str]
    area: Optional[str]

    # Crime-type-specific extras (only populated for the matching subtype)
    cause_of_death: Optional[str] = None
    body_location: Optional[str] = None
    time_of_death: Optional[str] = None
    medical_exam: Optional[str] = None
    stolen_items: Optional[str] = None
    stolen_value: Optional[float] = None

    # ── Related rows ────────────────────────────────────────────────
    suspects:  List[SuspectCtx]      = field(default_factory=list)
    victims:   List[VictimCtx]       = field(default_factory=list)
    witnesses: List[WitnessCtx]      = field(default_factory=list)
    evidence:  List[EvidenceCtx]     = field(default_factory=list)
    entities:  List[EntityCtx]       = field(default_factory=list)
    linked:    List[LinkedCaseCtx]   = field(default_factory=list)
    timeline:  List[TimelineCtx]     = field(default_factory=list)

    # ── Validation indices (pre-built so § 6 is O(1) per check) ─────
    all_cnics_seen:    Set[str] = field(default_factory=set)
    all_case_ids_seen: Set[str] = field(default_factory=set)
    all_suspect_ids:   Set[str] = field(default_factory=set)
    all_victim_ids:    Set[str] = field(default_factory=set)
    all_witness_ids:   Set[str] = field(default_factory=set)
    all_evidence_ids:  Set[str] = field(default_factory=set)
    linked_case_ids:   Set[str] = field(default_factory=set)


# ─────────────────────────────────────────────────────────────────────────────
# Builder
# ─────────────────────────────────────────────────────────────────────────────

def build_case_context(
    db: Session,
    *,
    user: User,
    case_id: str,
) -> CaseContext:
    """Pull everything we need for one case in as few queries as possible.

    We use the existing `_resolve_case` helper to keep the auth scope
    consistent with the rest of the case_*_service modules — a user who
    can't see a case via the normal endpoints can't generate leads for
    it either.
    """
    # _resolve_case already issues the auth check and raises 404/403 if
    # the user doesn't own it. We then re-fetch with the full set of
    # eager-loaded relations we need; an `options()` chain on the
    # original query would be cleaner but _resolve_case is shared by
    # ~20 endpoints and we don't want to change its shape.
    case = _resolve_case(db, user=user, case_id=case_id)

    full = (
        db.query(Case)
        .options(
            joinedload(Case.case_type),
            joinedload(Case.case_status),
            joinedload(Case.severity),
            joinedload(Case.location),
            joinedload(Case.weapon),
            joinedload(Case.assigned_to),
            joinedload(Case.suspects).joinedload(CaseSuspect.person),
            joinedload(Case.suspects).joinedload(CaseSuspect.status),
            joinedload(Case.victims).joinedload(CaseVictim.person),
            joinedload(Case.witnesses).joinedload(CaseWitness.person),
            joinedload(Case.witnesses).joinedload(CaseWitness.credibility),
            joinedload(Case.evidences).joinedload(Evidence.type),
            joinedload(Case.extracted_entities),
            joinedload(Case.murder_details),
        )
        .filter(Case.id == case.id)
        .first()
    )
    if not full:
        # Shouldn't happen — _resolve_case just confirmed it exists.
        raise RuntimeError(f"Lost case {case_id} between resolve and load")

    ctx = CaseContext(
        case_id          = full.case_id,
        case_internal_id = full.id,
        case_title       = full.case_title,
        case_type        = full.case_type.label if full.case_type else None,
        case_status      = full.case_status.label if full.case_status else None,
        fir_number       = full.fir_number,
        incident_date    = full.incident_date,
        incident_time    = full.incident_time,
        description      = full.description,
        motive           = full.motive,
        modus            = full.modus,
        weapon_used      = full.weapon.label if full.weapon else None,
        ppc_sections     = full.ppc_sections,
        reporting_officer= full.reporting_officer,
        province         = full.location.province.label if (full.location and full.location.province) else None,
        city             = full.location.city.name if (full.location and full.location.city) else None,
        area             = full.location.area if full.location else None,
    )
    ctx.all_case_ids_seen.add(full.case_id)

    # ── Crime-type extras ──────────────────────────────────────────
    if full.murder_details:
        ctx.cause_of_death = full.murder_details.cause_of_death
        ctx.body_location  = full.murder_details.body_location
        ctx.time_of_death  = full.murder_details.time_of_death
    # SexualAssaultDetails / TheftDetails are 1:1 — fetch separately
    # only if needed (they're not on the joinedload chain). We keep
    # this opt-in to avoid bloating every context build.
    sa = db.query(SexualAssaultDetails).filter(
        SexualAssaultDetails.case_id_fk == full.id
    ).first()
    if sa:
        ctx.medical_exam = sa.medical_exam
    th = db.query(TheftDetails).filter(
        TheftDetails.case_id_fk == full.id
    ).first()
    if th:
        ctx.stolen_items = th.stolen_items
        ctx.stolen_value = th.stolen_value

    # ── Suspects ───────────────────────────────────────────────────
    for s in (full.suspects or []):
        sus_id = s.suspect_id or f"SUS-{full.case_id}-{s.id:02d}"
        person = s.person
        cnic   = person.cnic if person else None
        sus = SuspectCtx(
            suspect_id     = sus_id,
            name           = person.full_name if person else None,
            cnic           = cnic,
            age            = person.age if person else None,
            relation       = s.relation_to_case,
            criminal_record= bool(s.criminal_record),
            affiliations   = s.known_affiliations,
            vehicle        = s.vehicle_description,
            reason         = s.reason,
        )
        ctx.suspects.append(sus)
        ctx.all_suspect_ids.add(sus_id)
        if cnic:
            ctx.all_cnics_seen.add(cnic)

    # ── Victims ────────────────────────────────────────────────────
    for v in (full.victims or []):
        vic_id = v.victim_id or f"VIC-{full.case_id}-{v.id:02d}"
        person = v.person
        cnic   = person.cnic if person else None
        ctx.victims.append(VictimCtx(
            victim_id = vic_id,
            name      = person.full_name if person else None,
            cnic      = cnic,
            age       = person.age if person else None,
            status    = v.status.label if v.status else None,
        ))
        ctx.all_victim_ids.add(vic_id)
        if cnic:
            ctx.all_cnics_seen.add(cnic)

    # ── Witnesses ──────────────────────────────────────────────────
    for w in (full.witnesses or []):
        wit_id = w.witness_id or f"WIT-{full.case_id}-{w.id:02d}"
        person = w.person
        cnic   = person.cnic if person else None
        is_anon = bool(getattr(w, "anonymous", False))
        ctx.witnesses.append(WitnessCtx(
            witness_id = wit_id,
            name       = None if is_anon else (person.full_name if person else None),
            cnic       = None if is_anon else cnic,
            credibility= w.credibility.label if w.credibility else None,
            statement  = w.description,
        ))
        ctx.all_witness_ids.add(wit_id)
        if cnic and not is_anon:
            ctx.all_cnics_seen.add(cnic)

    # ── Evidence ───────────────────────────────────────────────────
    for e in (full.evidences or []):
        ev_id = e.evidence_id
        ctx.evidence.append(EvidenceCtx(
            evidence_id = ev_id,
            type_label  = e.type.label if e.type else "Other",
            description = e.description,
        ))
        ctx.all_evidence_ids.add(ev_id)

    # ── Extracted entities (FIR NER output) ────────────────────────
    # These are entities mentioned in the FIR text that the system
    # extracted but the investigator hasn't formalised yet — exactly
    # the kind of lead opportunity the LLM should latch onto.
    for ent in (full.extracted_entities or []):
        if not ent.value:
            continue
        ctx.entities.append(EntityCtx(
            type  = (ent.entity_type or "OTHER").upper(),
            value = ent.value,
            notes = getattr(ent, "context", None),
        ))

    # ── Linked cases (top N by similarity) ─────────────────────────
    links = (
        db.query(CaseLink)
        .filter(CaseLink.source_case_id == full.id)
        .order_by(desc(CaseLink.similarity_score))
        .limit(MAX_LINKED_CASES)
        .all()
    )
    for link in links:
        other = (
            db.query(Case)
            .options(joinedload(Case.assigned_to))
            .filter(Case.id == link.target_case_id)
            .first()
        )
        if not other or other.is_deleted:
            continue
        inv_name = "Unknown"
        if other.assigned_to and other.assigned_to.user:
            rank = (other.assigned_to.rank or "").strip()
            inv_name = f"{rank}. {other.assigned_to.user.username}" if rank else other.assigned_to.user.username
        ctx.linked.append(LinkedCaseCtx(
            case_id          = other.case_id,
            title            = other.case_title,
            incident_date    = other.incident_date,
            similarity_score = float(link.similarity_score or 0.0),
            link_type        = link.link_type,
            explanation      = link.explanation,
            investigator_name= inv_name,
        ))
        ctx.linked_case_ids.add(other.case_id)
        ctx.all_case_ids_seen.add(other.case_id)

    # ── Timeline (recent events) ───────────────────────────────────
    tl_rows = (
        db.query(TimelineEvent)
        .filter(TimelineEvent.case_id_fk == full.id)
        .order_by(desc(TimelineEvent.created_at))
        .limit(MAX_TIMELINE_EVENTS)
        .all()
    )
    # Reverse so the prompt reads in chronological order — easier for
    # the LLM to follow a narrative arc.
    for t in reversed(tl_rows):
        ctx.timeline.append(TimelineCtx(
            event_date  = t.event_date,
            event_time  = t.event_time,
            title       = t.title or "",
            description = t.description,
        ))

    return ctx


# ─────────────────────────────────────────────────────────────────────────────
# Sanity check — refuse to call the LLM on cases too thin for grounded leads
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ContextDepthReport:
    """Result of the cheap pre-flight check we run before spending 10s
    on an LLM call that's doomed to produce nothing useful."""
    is_deep_enough: bool
    score: int                 # 0-10
    reasons: List[str]


def assess_context_depth(ctx: CaseContext) -> ContextDepthReport:
    """Decide whether the context is rich enough for grounded leads.

    Heuristic: we need at least SOME structured data the model can
    cite. A case with only a title and no entities, suspects, or links
    will produce noise no matter how good the prompt is.
    """
    score = 0
    reasons = []

    if ctx.suspects:
        score += 3
        reasons.append(f"{len(ctx.suspects)} suspect(s) on record")
    if ctx.victims:
        score += 1
        reasons.append(f"{len(ctx.victims)} victim(s) on record")
    if ctx.witnesses:
        score += 1
        reasons.append(f"{len(ctx.witnesses)} witness(es) on record")
    if ctx.evidence:
        score += 2
        reasons.append(f"{len(ctx.evidence)} evidence item(s)")
    if ctx.entities:
        score += 2
        reasons.append(f"{len(ctx.entities)} extracted entit(y/ies) from FIR")
    if ctx.linked:
        score += 2
        reasons.append(f"{len(ctx.linked)} linked case(s)")
    if ctx.description and len(ctx.description) >= 100:
        score += 1
        reasons.append("substantial case description")

    is_deep = score >= 3
    if not is_deep:
        reasons.insert(0, "Insufficient grounded data for meaningful leads")

    return ContextDepthReport(
        is_deep_enough=is_deep,
        score=score,
        reasons=reasons,
    )