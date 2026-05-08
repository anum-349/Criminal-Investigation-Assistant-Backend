from datetime import datetime
from typing import List, Optional, Dict, Tuple
from collections import Counter

from sqlalchemy import or_, func, desc
from sqlalchemy.orm import Session, joinedload, aliased
from fastapi import Request

from models import (
    User, Investigator, Person,
    Case, CaseStatus, CaseType, Severity,
    CaseSuspect, SuspectStatus,
    CaseVictim, VictimStatus,
    CaseWitness, WitnessCredibility,
    Lead, LeadStatus, LeadType,
    Location, City, Province,
)
from services import audit_service as audit
from schemas.search_schema import (
    CaseSearchRow, SuspectSearchRow, VictimSearchRow,
    WitnessSearchRow, LeadSearchRow, LocationSearchRow,
    SearchCounts, SearchResponse,
)


# Per-category cap. Page-level "5 per page" pagination across all categories
# means a cap around 50 still gives the user up to 60 result pages worth
# of breadth — which is plenty for an exploratory search.
PER_CATEGORY_LIMIT = 50


# ─── Helpers ────────────────────────────────────────────────────────────────

def _scope_cases(query, user: User):
    """Apply ownership scope on the joined Case object."""
    if user.role == "admin":
        return query
    return query.filter(Case.assigned_investigator_id == user.id)


def _format_investigator_name(case: Case) -> str:
    inv = case.assigned_to
    if not inv or not inv.user:
        return "—"
    rank = (inv.rank or "").strip()
    name = inv.user.username
    return f"{rank}. {name}" if rank else name


def _format_location_str(case: Case) -> str:
    loc = case.location
    if not loc:
        return "—"
    parts = []
    if loc.area: parts.append(loc.area)
    if loc.city and loc.city.name: parts.append(loc.city.name)
    if parts: return ", ".join(parts)
    return loc.display_address or loc.full_address or "—"


def _ymd(dt) -> str:
    return dt.strftime("%Y-%m-%d") if dt else ""


# ─── Per-category searches ─────────────────────────────────────────────────

def _search_cases(db: Session, *, user: User, q: str) -> List[CaseSearchRow]:
    """Match against case_id / title / fir_number / complainant / location /
    case-type label / investigator's username."""
    inv_user = aliased(User)

    query = (
        db.query(Case)
        .filter(Case.is_deleted == False)  # noqa: E712
        .outerjoin(Case.location)
        .outerjoin(Location.city)
        .outerjoin(Case.assigned_to)
        .outerjoin(inv_user, Investigator.user)
        .options(
            joinedload(Case.case_type),
            joinedload(Case.case_status),
            joinedload(Case.assigned_to).joinedload(Investigator.user),
            joinedload(Case.location).joinedload(Location.city),
        )
    )
    query = _scope_cases(query, user)

    if q:
        like = f"%{q}%"
        query = query.outerjoin(CaseType, Case.case_type_id == CaseType.id).filter(
            or_(
                Case.case_id.ilike(like),
                Case.case_title.ilike(like),
                Case.fir_number.ilike(like),
                Case.complainant_name.ilike(like),
                City.name.ilike(like),
                Location.area.ilike(like),
                CaseType.label.ilike(like),
                inv_user.username.ilike(like),
            )
        )

    rows = (
        query.order_by(desc(Case.updated_at))
             .distinct()
             .limit(PER_CATEGORY_LIMIT)
             .all()
    )

    return [
        CaseSearchRow(
            id=c.case_id,
            title=c.case_title,
            status=c.case_status.label if c.case_status else "—",
            created=_ymd(c.created_at),
            investigator=_format_investigator_name(c),
            complainant=c.complainant_name,
            fir_upload=_ymd(c.created_at),
            fir_id=c.fir_number,
            offense_type=c.case_type.label if c.case_type else "—",
            location=_format_location_str(c),
        )
        for c in rows
    ]


def _search_suspects(db: Session, *, user: User, q: str) -> List[SuspectSearchRow]:
    query = (
        db.query(CaseSuspect)
        .join(Case, CaseSuspect.case_id_fk == Case.id)
        .filter(Case.is_deleted == False)  # noqa: E712
        .options(
            joinedload(CaseSuspect.person),
            joinedload(CaseSuspect.status),
            joinedload(CaseSuspect.case),
        )
    )
    query = _scope_cases(query, user)

    if q:
        like = f"%{q}%"
        query = (
            query.outerjoin(CaseSuspect.person)
                 .outerjoin(SuspectStatus, CaseSuspect.status_id == SuspectStatus.id)
                 .filter(
                     or_(
                         CaseSuspect.suspect_id.ilike(like),
                         Person.full_name.ilike(like),
                         Person.cnic.ilike(like),
                         Case.case_id.ilike(like),
                         CaseSuspect.reason.ilike(like),
                         CaseSuspect.alibi.ilike(like),
                         CaseSuspect.relation_to_case.ilike(like),
                         SuspectStatus.label.ilike(like),
                     )
                 )
        )

    rows = (
        query.order_by(desc(CaseSuspect.updated_at))
             .distinct()
             .limit(PER_CATEGORY_LIMIT)
             .all()
    )

    return [
        SuspectSearchRow(
            id=s.suspect_id,
            name=s.person.full_name if s.person else None,
            case_id=s.case.case_id if s.case else "—",
            case_title=s.case.case_title if s.case else "—",
            status=s.status.label if s.status else "—",
            relation=s.relation_to_case,
            reason=s.reason,
            alibi=s.alibi,
        )
        for s in rows
    ]


def _search_victims(db: Session, *, user: User, q: str) -> List[VictimSearchRow]:
    query = (
        db.query(CaseVictim)
        .join(Case, CaseVictim.case_id_fk == Case.id)
        .filter(Case.is_deleted == False)  # noqa: E712
        .options(
            joinedload(CaseVictim.person),
            joinedload(CaseVictim.status),
            joinedload(CaseVictim.case),
        )
    )
    query = _scope_cases(query, user)

    if q:
        like = f"%{q}%"
        query = (
            query.outerjoin(CaseVictim.person)
                 .outerjoin(VictimStatus, CaseVictim.status_id == VictimStatus.id)
                 .filter(
                     or_(
                         CaseVictim.victim_id.ilike(like),
                         Person.full_name.ilike(like),
                         Person.cnic.ilike(like),
                         Person.gender.ilike(like),
                         Case.case_id.ilike(like),
                         VictimStatus.label.ilike(like),
                         CaseVictim.injury_type.ilike(like),
                     )
                 )
        )

    rows = (
        query.order_by(desc(CaseVictim.updated_at))
             .distinct()
             .limit(PER_CATEGORY_LIMIT)
             .all()
    )

    return [
        VictimSearchRow(
            id=v.victim_id,
            name=v.person.full_name if v.person else None,
            case_id=v.case.case_id if v.case else "—",
            case_title=v.case.case_title if v.case else "—",
            age=v.person.age if v.person else None,
            gender=v.person.gender if v.person else None,
            contact=v.person.contact if v.person else None,
            status=v.status.label if v.status else "—",
            injury_type=v.injury_type,
        )
        for v in rows
    ]


def _search_witnesses(db: Session, *, user: User, q: str) -> List[WitnessSearchRow]:
    query = (
        db.query(CaseWitness)
        .join(Case, CaseWitness.case_id_fk == Case.id)
        .filter(Case.is_deleted == False)  # noqa: E712
        .options(
            joinedload(CaseWitness.person),
            joinedload(CaseWitness.credibility),
            joinedload(CaseWitness.case),
        )
    )
    query = _scope_cases(query, user)

    if q:
        like = f"%{q}%"
        # NOTE: in models.py, `CaseWitness.credibility` is a relationship,
        # NOT the free-text column (the relationship overrode the column
        # because both share the name `credibility`). So we search the
        # joined WitnessCredibility.label instead, which is what users
        # actually see ("High", "Medium", "Low", etc.).
        query = (
            query.outerjoin(CaseWitness.person)
                 .outerjoin(
                     WitnessCredibility,
                     CaseWitness.credibility_id == WitnessCredibility.id,
                 )
                 .filter(
                     or_(
                         CaseWitness.witness_id.ilike(like),
                         Person.full_name.ilike(like),
                         Person.cnic.ilike(like),
                         Case.case_id.ilike(like),
                         CaseWitness.description.ilike(like),
                         CaseWitness.relation_to_case.ilike(like),
                         WitnessCredibility.label.ilike(like),
                     )
                 )
        )

    rows = (
        query.order_by(desc(CaseWitness.updated_at))
             .distinct()
             .limit(PER_CATEGORY_LIMIT)
             .all()
    )

    out: List[WitnessSearchRow] = []
    for w in rows:
        # `w.credibility` is a relationship to WitnessCredibility (or None).
        cred_label = w.credibility.label if w.credibility else "—"
        # Anonymous witnesses have name/contact suppressed.
        is_anon = bool(getattr(w, "anonymous", False))
        out.append(
            WitnessSearchRow(
                id=w.witness_id,
                name=None if is_anon else (w.person.full_name if w.person else None),
                case_id=w.case.case_id if w.case else "—",
                case_title=w.case.case_title if w.case else "—",
                statement=w.description,
                credibility=cred_label,
                contact=None if is_anon else (w.person.contact if w.person else None),
            )
        )
    return out


def _search_leads(db: Session, *, user: User, q: str) -> List[LeadSearchRow]:
    query = (
        db.query(Lead)
        .join(Case, Lead.case_id_fk == Case.id)
        .filter(Case.is_deleted == False)  # noqa: E712
        .options(
            joinedload(Lead.case),
            joinedload(Lead.type),
            joinedload(Lead.status),
            joinedload(Lead.severity),
        )
    )
    query = _scope_cases(query, user)

    if q:
        like = f"%{q}%"
        query = (
            query.outerjoin(LeadType, Lead.type_id == LeadType.id)
                 .outerjoin(LeadStatus, Lead.status_id == LeadStatus.id)
                 .outerjoin(Severity, Lead.severity_id == Severity.id)
                 .filter(
                     or_(
                         Lead.lead_id.ilike(like),
                         Case.case_id.ilike(like),
                         Lead.description.ilike(like),
                         LeadType.label.ilike(like),
                         LeadStatus.label.ilike(like),
                         Severity.label.ilike(like),
                     )
                 )
        )

    rows = (
        query.order_by(desc(Lead.generated_at))
             .distinct()
             .limit(PER_CATEGORY_LIMIT)
             .all()
    )

    return [
        LeadSearchRow(
            id=l.lead_id,
            case_id=l.case.case_id if l.case else "—",
            case_title=l.case.case_title if l.case else "—",
            description=l.description,
            type=l.type.label if l.type else "—",
            severity=l.severity.label if l.severity else "—",
            status=l.status.label if l.status else "—",
            date=_ymd(l.generated_at),
        )
        for l in rows
    ]


def _search_locations(db: Session, *, user: User, q: str) -> List[LocationSearchRow]:
    """
    Locations are aggregated rows (city + area). One row per distinct
    (city, area) pair, with the count of cases that hit it. The user's
    query is matched against the area / city / case_type / case_id.
    """
    query = (
        db.query(
            Location.area.label("area"),
            City.name.label("city_name"),
            Case.id.label("case_pk"),
            Case.case_id.label("case_id"),
            Case.updated_at.label("updated_at"),
            CaseType.label.label("crime_label"),
            Severity.label.label("severity_label"),
            Severity.rank.label("severity_rank"),
        )
        .select_from(Case)
        .join(Location, Location.case_id_fk == Case.id)
        .join(City, Location.city_id == City.id)
        .outerjoin(CaseType, Case.case_type_id == CaseType.id)
        .outerjoin(Severity, Case.priority_id == Severity.id)
        .filter(Case.is_deleted == False)  # noqa: E712
    )
    query = _scope_cases(query, user)

    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                Location.area.ilike(like),
                City.name.ilike(like),
                CaseType.label.ilike(like),
                Case.case_id.ilike(like),
            )
        )

    rows = query.all()

    # Group by (area, city)
    groups: Dict[Tuple[str, str], dict] = {}
    for r in rows:
        area = r.area or ""
        city_name = r.city_name or ""
        key = (area, city_name)
        g = groups.setdefault(key, {
            "area": area,
            "city": city_name,
            "cases": [],
            "case_ids": [],
            "crime_types": [],
            "last_incident": None,
            "max_severity_rank": -1,
            "max_severity_label": "Low",
        })
        g["cases"].append(r.case_pk)
        g["case_ids"].append(r.case_id)
        if r.crime_label:
            g["crime_types"].append(r.crime_label)
        if r.updated_at and (g["last_incident"] is None or r.updated_at > g["last_incident"]):
            g["last_incident"] = r.updated_at
        if r.severity_rank is not None and r.severity_rank > g["max_severity_rank"]:
            g["max_severity_rank"] = r.severity_rank
            g["max_severity_label"] = r.severity_label or "Low"

    out: List[LocationSearchRow] = []
    for idx, (key, g) in enumerate(groups.items(), start=1):
        # most-common crime type wins the column
        crime_top = (
            Counter(g["crime_types"]).most_common(1)[0][0]
            if g["crime_types"] else "—"
        )
        area_str = ", ".join([p for p in [g["area"], g["city"]] if p]) or "—"
        out.append(
            LocationSearchRow(
                id=f"LOC-{idx:03d}",
                area=area_str,
                crime_type=crime_top,
                cases=len(set(g["cases"])),
                last_incident=_ymd(g["last_incident"]),
                severity=g["max_severity_label"],
                case_ids=sorted(set(g["case_ids"])),
            )
        )

    # Stable order: most cases first, then most recent.
    out.sort(key=lambda r: (-r.cases, r.last_incident), reverse=False)
    return out[:PER_CATEGORY_LIMIT]


# ─── Public entry point ────────────────────────────────────────────────────

def search_all(
    db: Session,
    *,
    user: User,
    q: str,
    request: Optional[Request] = None,
) -> SearchResponse:
    """Run all six category searches and assemble the response."""
    q = (q or "").strip()

    cases     = _search_cases(db,     user=user, q=q)
    suspects  = _search_suspects(db,  user=user, q=q)
    victims   = _search_victims(db,   user=user, q=q)
    witnesses = _search_witnesses(db, user=user, q=q)
    leads     = _search_leads(db,     user=user, q=q)
    locations = _search_locations(db, user=user, q=q)

    counts = SearchCounts(
        cases=len(cases),
        suspects=len(suspects),
        victims=len(victims),
        witnesses=len(witnesses),
        leads=len(leads),
        locations=len(locations),
    )
    counts.all = (
        counts.cases + counts.suspects + counts.victims +
        counts.witnesses + counts.leads + counts.locations
    )

    # Audit — never let logging failure break the response.
    try:
        audit.log_event(
            db,
            user_id=user.id,
            action="SEARCH",
            module="Case Management",
            detail=(
                f"Global search q='{q or '(empty)'}'. "
                f"Matched cases={counts.cases}, suspects={counts.suspects}, "
                f"victims={counts.victims}, witnesses={counts.witnesses}, "
                f"leads={counts.leads}, locations={counts.locations}."
            ),
            target_type="search",
            target_id=None,
            request=request,
        )
        db.commit()
    except Exception:
        db.rollback()

    return SearchResponse(
        query=q,
        counts=counts,
        cases=cases,
        suspects=suspects,
        victims=victims,
        witnesses=witnesses,
        leads=leads,
        locations=locations,
    )