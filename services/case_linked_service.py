from typing import List, Optional, Tuple
from datetime import date, datetime
from sqlalchemy import or_, desc, func
from sqlalchemy.orm import Session, joinedload, aliased
from fastapi import Request, HTTPException

from models import (
    User, Investigator,
    Case, CaseStatus,
    CaseLink,
)
from services import audit_service as audit
from schemas.case_linked_schema import (
    LinkedCaseRow,
    LinkedRelationOption,
    LinkedStatusOption,
    CaseLinkedCasesList,
)
from services.service_helper import _resolve_case

LINK_TYPE_LABEL = {
    # link_type code           → human-readable label                  variant
    "SAME_SUSPECT":           ("Similar suspect description",          "warning"),
    "SAME_LOCATION":          ("Same building location",               "success"),
    "SAME_VICTIM":            ("Involves victim's colleague",          "default"),
    "SUSPECT_MOVEMENT":       ("Possible suspect movement pattern",    "destructive"),
    "SCENE_PROXIMITY":        ("Repeated crime scene proximity",       "warning"),
    "SAME_WEAPON":            ("Common weapon used",                   "warning"),
    "SAME_MO":                ("Same modus operandi",                  "warning"),
    "COMMON_EVIDENCE":        ("Common evidence",                      "success"),
    "RELATED_INCIDENT":       ("Related incident",                     "default"),
    "OTHER":                  ("Other relation",                       "default"),
}

def _status_variant(label: str) -> str:
    s = (label or "").lower()
    if "open" in s or "active" in s or "under" in s:
        return "success"
    if "closed" in s or "solved" in s:
        return "destructive"
    if "pending" in s:
        return "warning"
    return "default"

def _format_investigator(case: Case) -> str:
    """'Insp. A. Khan' or '—'."""
    inv = case.assigned_to
    if not inv or not inv.user:
        return "—"
    rank = (inv.rank or "").strip()
    name = inv.user.username
    return f"{rank}. {name}" if rank else name


def _format_date(d: Optional[datetime]) -> str:
    """Match the mock data's MM/DD/YYYY format. Frontend renders it as-is."""
    if not d:
        return "—"
    return d.strftime("%m/%d/%Y")


def _row_from_link(link: CaseLink, other_case: Case) -> LinkedCaseRow:
    """Build a LinkedCaseRow from a CaseLink + the 'other' case."""
    label, _variant = LINK_TYPE_LABEL.get(
        link.link_type,
        (link.link_type.replace("_", " ").title(), "default"),
    )

    relation_text = (link.explanation or "").strip() or label

    return LinkedCaseRow(
        id=other_case.case_id,
        linkedCaseId=other_case.case_id,
        title=other_case.case_title or "—",
        investigator=_format_investigator(other_case),
        registerDate=_format_date(other_case.created_at),
        status=other_case.case_status.label if other_case.case_status else "—",
        relation=relation_text,
        linkType=link.link_type,
        similarityScore=link.similarity_score,
        explanation=link.explanation,
    )


def _gather_links(db: Session, case: Case) -> List[Tuple[CaseLink, Case]]:
    """Pull both directions of the link, eager-loading what we need."""
    OtherCaseAlias = aliased(Case)

    out_q = (
        db.query(CaseLink, OtherCaseAlias)
        .join(OtherCaseAlias, CaseLink.target_case_id == OtherCaseAlias.id)
        .filter(
            CaseLink.source_case_id == case.id,
            OtherCaseAlias.is_deleted == False,  # noqa: E712
        )
        .options(
            joinedload(CaseLink.source_case),
            joinedload(CaseLink.target_case).joinedload(Case.case_status),
            joinedload(CaseLink.target_case).joinedload(Case.assigned_to).joinedload(Investigator.user),
        )
    )

    in_q = (
        db.query(CaseLink, OtherCaseAlias)
        .join(OtherCaseAlias, CaseLink.source_case_id == OtherCaseAlias.id)
        .filter(
            CaseLink.target_case_id == case.id,
            OtherCaseAlias.is_deleted == False,  # noqa: E712
        )
        .options(
            joinedload(CaseLink.target_case),
            joinedload(CaseLink.source_case).joinedload(Case.case_status),
            joinedload(CaseLink.source_case).joinedload(Case.assigned_to).joinedload(Investigator.user),
        )
    )

    pairs: List[Tuple[CaseLink, Case]] = []
    pairs.extend(out_q.all())
    pairs.extend(in_q.all())


    seen = set()
    deduped: List[Tuple[CaseLink, Case]] = []
    for link, other in pairs:
        key = (other.id, link.link_type)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((link, other))

    deduped.sort(key=lambda p: p[0].created_at or datetime.min, reverse=True)
    return deduped


def _apply_filters(
    rows: List[LinkedCaseRow],
    *,
    search: str,
    relation: str,
    on_date: Optional[date],
    status: str,
) -> List[LinkedCaseRow]:
    """In-memory filter pass. Linked-case lists are small (rarely 50+),
    so we filter Python-side to keep the SQL clean. If volumes grow, push
    these into the query in _gather_links."""
    out = rows

    if search:
        kw = search.lower()
        out = [
            r for r in out
            if kw in r.id.lower()
            or kw in (r.title or "").lower()
            or kw in (r.relation or "").lower()
        ]

    if relation:
        rel = relation.lower()
        out = [
            r for r in out
            if rel in (r.linkType or "").lower()
            or rel in (r.relation or "").lower()
        ]

    if status and status != "all":
        out = [r for r in out if (r.status or "").lower() == status.lower()]

    if on_date:
        target = on_date.strftime("%m/%d/%Y")
        out = [r for r in out if r.registerDate == target]

    return out


def list_linked_cases(
    db: Session,
    *,
    user: User,
    request: Optional[Request],
    case_id: str,
    search: str = "",
    relation: str = "",
    on_date: Optional[date] = None,
    status: str = "all",
    page: int = 1,
    page_size: int = 5,
) -> CaseLinkedCasesList:
    """Returns a paginated list payload the React page can render in one shot."""
    case = _resolve_case(db, user=user, case_id=case_id)

    pairs = _gather_links(db, case)
    all_rows = [_row_from_link(link, other) for link, other in pairs]

    filtered = _apply_filters(
        all_rows,
        search=(search or "").strip(),
        relation=(relation or "").strip(),
        on_date=on_date,
        status=(status or "all").strip(),
    )

    total = len(filtered)
    page = max(1, page)
    page_size = max(1, min(page_size, 50))
    start = (page - 1) * page_size
    end = start + page_size
    items = filtered[start:end]

    seen_types = []
    for r in all_rows:
        if r.linkType and r.linkType not in seen_types:
            seen_types.append(r.linkType)

    relation_options: List[LinkedRelationOption] = [
        LinkedRelationOption(value="", label="Relation", variant="default"),
    ]
    for code in seen_types:
        label, variant = LINK_TYPE_LABEL.get(
            code, (code.replace("_", " ").title(), "default")
        )
        relation_options.append(
            LinkedRelationOption(value=code, label=label, variant=variant)
        )


    seen_statuses = []
    for r in all_rows:
        if r.status and r.status not in seen_statuses:
            seen_statuses.append(r.status)

    status_options: List[LinkedStatusOption] = [
        LinkedStatusOption(value="all", label="Status All", variant="default"),
    ]
    for s in seen_statuses:
        status_options.append(
            LinkedStatusOption(value=s, label=s, variant=_status_variant(s))
        )

    try:
        audit.log_event(
            db,
            user_id=user.id,
            action="VIEW",
            module="Case Management",
            detail=f"Viewed linked cases for '{case.case_id}' "
                   f"(found {len(all_rows)}, returning {len(items)}).",
            target_type="case",
            target_id=case.case_id,
            request=request,
        )
        db.commit()
    except Exception:
        db.rollback()

    return CaseLinkedCasesList(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        relation_options=relation_options,
        status_options=status_options,
    )