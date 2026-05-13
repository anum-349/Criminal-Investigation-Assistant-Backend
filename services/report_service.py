import secrets
from datetime import UTC, datetime, date
from typing import Optional, List

from sqlalchemy import func as sa_func, desc, or_
from sqlalchemy.orm import Session, joinedload
from fastapi import HTTPException, Request

from models import (
    User, Case, CaseSuspect, CaseVictim, CaseWitness,
    Lead, TimelineEvent, Location, Person,
    CaseType, Province, City, Severity,
    GeneratedReport,
)
from schemas.report_schema import (
    GenerateReportRequest, ReportData, ReportSection,
    ReportHistoryItem, ReportHistoryList,
)
from services import audit_service as audit
from services.service_helper import _resolve_case, _ymd
import logging

log = logging.getLogger(__name__)


# ── helpers ────────────────────────────────────────────────────────────────

def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _gen_report_id(db: Session) -> str:
    date_part = datetime.now(UTC).strftime("%Y%m%d")
    prefix    = f"RPT-{date_part}-"
    count     = db.query(GeneratedReport).filter(
        GeneratedReport.report_id.like(f"{prefix}%")
    ).count()
    return f"{prefix}{count + 1:03d}"


def _str(v) -> str:
    """Safe stringify for table cells."""
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, (date, datetime)):
        return v.strftime("%d %b %Y")
    return str(v)


# ── report builders ────────────────────────────────────────────────────────

def _build_case_summary(db: Session, filters) -> ReportData:
    case_id = (filters.caseId or "").strip()
    if not case_id:
        raise HTTPException(status_code=422, detail="caseId is required for Case Summary report")

    case = (
        db.query(Case)
        .options(
            joinedload(Case.case_type),
            joinedload(Case.case_status),
            joinedload(Case.priority),
            joinedload(Case.location).joinedload(Location.province),
            joinedload(Case.location).joinedload(Location.city),
            joinedload(Case.assigned_to),
            joinedload(Case.suspects).joinedload(CaseSuspect.person),
            joinedload(Case.suspects).joinedload(CaseSuspect.status),
            joinedload(Case.victims).joinedload(CaseVictim.person),
            joinedload(Case.victims).joinedload(CaseVictim.status),
            joinedload(Case.witnesses).joinedload(CaseWitness.person),
            joinedload(Case.evidences),
        )
        .filter(Case.case_id == case_id, Case.is_deleted == False)
        .first()
    )
    if not case:
        raise HTTPException(status_code=404, detail=f"Case '{case_id}' not found")

    loc = case.location

    # ── Overview section
    officer = "Unassigned"
    if case.assigned_to and case.assigned_to.user:
        u = case.assigned_to.user
        officer = f"{u.username} ({getattr(case.assigned_to, 'rank', '')})"

    days_open = (date.today() - case.incident_date).days if case.incident_date else "—"

    overview = ReportSection(
        heading="Case Overview",
        rows=[
            ["Case ID",          _str(case.case_id)],
            ["FIR Number",       _str(case.fir_number)],
            ["Type",             _str(case.case_type.label if case.case_type else None)],
            ["Status",           _str(case.case_status.label if case.case_status else None)],
            ["Priority",         _str(case.priority.label if case.priority else None)],
            ["Assigned Officer", officer],
            ["Incident Date",    _str(case.incident_date)],
            ["Reporting Date",   _str(case.reporting_date)],
            ["Days Open",        _str(days_open)],
            ["Province",         _str(loc.province.label if loc and loc.province else None)],
            ["City",             _str(loc.city.name if loc and loc.city else None)],
            ["Location",         _str(loc.full_address if loc else None)],
        ],
    )

    # ── Suspects section
    suspect_rows = [["Name", "CNIC", "Gender", "Status", "Relation"]]
    for s in case.suspects:
        p = s.person
        suspect_rows.append([
            _str(p.full_name if p else None),
            _str(p.cnic if p else None),
            _str(p.gender if p else None),
            _str(s.status.label if s.status else None),
            _str(s.relation_to_case),
        ])
    if len(suspect_rows) == 1:
        suspect_rows.append(["No suspects recorded", "—", "—", "—", "—"])

    # ── Victims section
    victim_rows = [["Name", "Gender", "Age", "Status", "Injury"]]
    for v in case.victims:
        p = v.person
        victim_rows.append([
            _str(p.full_name if p else None),
            _str(p.gender if p else None),
            _str(p.age if p else None),
            _str(v.status.label if v.status else None),
            _str(v.injury_type),
        ])
    if len(victim_rows) == 1:
        victim_rows.append(["No victims recorded", "—", "—", "—", "—"])

    # ── Evidence section
    evidence_rows = [["#", "Type", "Description", "Date Collected", "Collected By"]]
    for i, e in enumerate(case.evidences, 1):
        evidence_rows.append([
            str(i),
            _str(e.type.label if e.type else None),
            _str((e.description or "")[:60]),
            _str(e.date_collected),
            _str(e.collected_by),
        ])
    if len(evidence_rows) == 1:
        evidence_rows.append(["No evidence recorded", "—", "—", "—", "—"])

    # ── Witnesses section
    witness_rows = [["Name / Anon", "Gender", "Age", "Relation", "Credibility"]]
    for w in case.witnesses:
        p = w.person
        name = "Anonymous" if w.anonymous else _str(p.full_name if p else None)
        witness_rows.append([
            name,
            _str(p.gender if p else None),
            _str(p.age if p else None),
            _str(w.relation_to_case),
            _str(w.credibility.label if w.credibility else None),
        ])
    if len(witness_rows) == 1:
        witness_rows.append(["No witnesses recorded", "—", "—", "—", "—"])

    return ReportData(
        title=f"Case Summary Report — {case.case_id}",
        sections=[
            overview,
            ReportSection(heading="Suspects",  rows=suspect_rows),
            ReportSection(heading="Victims",   rows=victim_rows),
            ReportSection(heading="Witnesses", rows=witness_rows),
            ReportSection(heading="Evidence",  rows=evidence_rows),
        ],
    )


def _build_crime_hotspot(db: Session, filters) -> ReportData:
    date_from = _parse_date(filters.dateFrom)
    date_to   = _parse_date(filters.dateTo) or date.today()

    # Province breakdown
    q = (
        db.query(
            Province.label,
            sa_func.count(Case.id).label("total"),
        )
        .join(Location, Location.province_id == Province.id)
        .join(Case, Case.id == Location.case_id_fk)
        .filter(Case.is_deleted == False)
    )
    if date_from:
        q = q.filter(Case.incident_date >= date_from)
    if date_to:
        q = q.filter(Case.incident_date <= date_to)
    if filters.province and filters.province not in ("All Provinces", ""):
        q = q.filter(Province.label == filters.province)

    province_rows = [["Province", "Cases"]]
    for label, total in q.group_by(Province.label).order_by(desc("total")).all():
        province_rows.append([_str(label), _str(total)])
    if len(province_rows) == 1:
        province_rows.append(["No data", "0"])

    # Crime type breakdown
    q2 = (
        db.query(
            CaseType.label,
            sa_func.count(Case.id).label("total"),
        )
        .join(Case, Case.case_type_id == CaseType.id)
        .filter(Case.is_deleted == False)
    )
    if date_from:
        q2 = q2.filter(Case.incident_date >= date_from)
    if date_to:
        q2 = q2.filter(Case.incident_date <= date_to)
    if filters.crimeType and filters.crimeType not in ("All Types", ""):
        q2 = q2.filter(CaseType.label.ilike(f"%{filters.crimeType}%"))

    crime_rows = [["Crime Type", "Cases"]]
    for label, total in q2.group_by(CaseType.label).order_by(desc("total")).all():
        crime_rows.append([_str(label), _str(total)])
    if len(crime_rows) == 1:
        crime_rows.append(["No data", "0"])

    period = f"{_str(date_from) if date_from else 'All time'} — {_str(date_to)}"
    return ReportData(
        title=f"Crime Hotspot Report — {period}",
        sections=[
            ReportSection(heading="Cases by Province",   rows=province_rows),
            ReportSection(heading="Cases by Crime Type", rows=crime_rows),
        ],
    )


def _build_case_timeline(db: Session, filters) -> ReportData:
    case_id = (filters.caseId or "").strip()
    if not case_id:
        raise HTTPException(status_code=422, detail="caseId is required for Case Timeline report")

    case = (
        db.query(Case)
        .filter(Case.case_id == case_id, Case.is_deleted == False)
        .first()
    )
    if not case:
        raise HTTPException(status_code=404, detail=f"Case '{case_id}' not found")

    events = (
        db.query(TimelineEvent)
        .filter(TimelineEvent.case_id_fk == case.id)
        .order_by(TimelineEvent.event_date, TimelineEvent.event_time)
        .all()
    )

    event_rows = [["Date", "Time", "Event", "Officer", "Source"]]
    for ev in events:
        event_rows.append([
            _str(ev.event_date),
            _str(ev.event_time),
            _str(ev.title),
            _str(ev.officer_name),
            _str(ev.event_source),
        ])
    if len(event_rows) == 1:
        event_rows.append(["No events recorded", "—", "—", "—", "—"])

    return ReportData(
        title=f"Case Timeline Report — {case_id}",
        sections=[ReportSection(heading="Timeline Events", rows=event_rows)],
    )


def _build_leads_report(db: Session, filters) -> ReportData:
    case_id = (filters.caseId or "").strip()
    if not case_id:
        raise HTTPException(status_code=422, detail="caseId is required for AI Leads report")

    case = (
        db.query(Case)
        .filter(Case.case_id == case_id, Case.is_deleted == False)
        .first()
    )
    if not case:
        raise HTTPException(status_code=404, detail=f"Case '{case_id}' not found")

    min_conf = (filters.minConfidence or 60) / 100.0
    leads = (
        db.query(Lead)
        .options(joinedload(Lead.type), joinedload(Lead.status), joinedload(Lead.severity))
        .filter(
            Lead.case_id_fk == case.id,
            Lead.confidence >= min_conf,
        )
        .order_by(desc(Lead.confidence))
        .all()
    )

    lead_rows = [["#", "Type", "Description", "Confidence", "Status", "Next Step"]]
    for i, lead in enumerate(leads, 1):
        lead_rows.append([
            str(i),
            _str(lead.type.label if lead.type else None),
            _str((lead.description or "")[:80]),
            f"{lead.confidence * 100:.0f}%",
            _str(lead.status.label if lead.status else None),
            _str((lead.next_step or "")[:60]),
        ])
    if len(lead_rows) == 1:
        lead_rows.append(["No leads found", "—", "—", "—", "—", "—"])

    return ReportData(
        title=f"AI Leads Report — {case_id} (≥{int(filters.minConfidence or 60)}% confidence)",
        sections=[ReportSection(heading="Generated Leads", rows=lead_rows)],
    )


def _build_suspect_report(db: Session, filters) -> ReportData:
    date_from = _parse_date(filters.dateFrom)
    date_to   = _parse_date(filters.dateTo) or date.today()

    q = (
        db.query(CaseSuspect)
        .options(
            joinedload(CaseSuspect.person),
            joinedload(CaseSuspect.status),
            joinedload(CaseSuspect.case).joinedload(Case.case_type),
        )
        .join(Case, Case.id == CaseSuspect.case_id_fk)
        .filter(Case.is_deleted == False)
    )
    if date_from:
        q = q.filter(Case.incident_date >= date_from)
    if date_to:
        q = q.filter(Case.incident_date <= date_to)
    if filters.crimeType and filters.crimeType not in ("All Types", ""):
        q = q.join(CaseType, Case.case_type_id == CaseType.id) \
             .filter(CaseType.label.ilike(f"%{filters.crimeType}%"))

    suspects = q.order_by(desc(Case.incident_date)).all()

    suspect_rows = [["Suspect ID", "Name", "CNIC", "Gender", "Case ID", "Crime Type", "Status", "Arrested"]]
    for s in suspects:
        p = s.person
        suspect_rows.append([
            _str(s.suspect_id),
            _str(p.full_name if p else None),
            _str(p.cnic if p else None),
            _str(p.gender if p else None),
            _str(s.case.case_id if s.case else None),
            _str(s.case.case_type.label if s.case and s.case.case_type else None),
            _str(s.status.label if s.status else None),
            _str(s.arrested),
        ])
    if len(suspect_rows) == 1:
        suspect_rows.append(["No suspects found", "—", "—", "—", "—", "—", "—", "—"])

    period = f"{_str(date_from) if date_from else 'All time'} — {_str(date_to)}"
    return ReportData(
        title=f"Suspect Analysis Report — {period}",
        sections=[ReportSection(heading="Suspect Records", rows=suspect_rows)],
    )


# ── dispatch table ─────────────────────────────────────────────────────────

BUILDERS = {
    "case_summary":   _build_case_summary,
    "crime_hotspot":  _build_crime_hotspot,
    "case_timeline":  _build_case_timeline,
    "leads_report":   _build_leads_report,
    "suspect_report": _build_suspect_report,
}


# ── public API ─────────────────────────────────────────────────────────────

def generate_report(
    db: Session, *,
    user: User,
    body: GenerateReportRequest,
    request: Optional[Request],
) -> ReportData:
    builder = BUILDERS.get(body.reportType)
    if not builder:
        raise HTTPException(status_code=422, detail="Unknown report type")

    data = builder(db, body.filters)

    # Persist to generated_reports
    try:
        record = GeneratedReport(
            report_id        = _gen_report_id(db),
            report_type      = body.reportType,
            filters          = body.filters.model_dump(),
            format           = body.filters.format,
            generated_by_id  = user.id,
            generated_at     = datetime.now(UTC),
        )
        db.add(record)
        audit.log_event(
            db, user_id=user.id, action="CREATE", module="Reports",
            detail      = f"Report '{body.reportType}' generated",
            target_type = "report", target_id=record.report_id, request=request,
        )
        db.commit()
    except Exception:
        db.rollback()
        log.warning("Could not persist report record", exc_info=True)
        # Non-fatal — still return the data

    return data


def get_report_history(
    db: Session, *,
    user: User,
    page: int = 1,
    page_size: int = 10,
    request: Optional[Request],
) -> ReportHistoryList:
    q = (
        db.query(GeneratedReport)
        .filter(GeneratedReport.generated_by_id == user.id)
        .order_by(desc(GeneratedReport.generated_at))
    )
    total = q.count()
    rows  = q.limit(page_size).offset((page - 1) * page_size).all()
    return ReportHistoryList(
        items=[ReportHistoryItem.model_validate(r) for r in rows],
        total=total,
    )