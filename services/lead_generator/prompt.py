from __future__ import annotations

from datetime import date
from textwrap import dedent
from typing import List, Optional

from services.lead_generator.context import (
    CaseContext, EntityCtx, EvidenceCtx, ForensicFindingCtx, GapCtx,
    LinkedCaseCtx, NearbyDensityCtx, NearbyIncidentCtx, NoteCtx,
    StatementCtx, SuspectCtx, TimelineCtx, VictimCtx, WitnessCtx,
    CompletenessCtx,
)

ALLOWED_LEAD_TYPES = [
    "Suspect Match",    
    "Suspect Pattern",  
    "Location Pattern", 
    "Case Linkage",     
    "CCTV Analysis",    
    "Fingerprint Match",
    "DNA Match",        
    "Phone / Digital",  
    "Forensic Analysis",
    "Witness Lead", 
    "Informant Tip",
    "Vehicle Match",
    "Weapon Match", 
    "Other",        
]

ALLOWED_SEVERITIES = ["Low", "Normal", "High", "Critical"]

SYSTEM_PROMPT = dedent(f"""\
You are a senior criminal investigation analyst assisting Pakistani police investigators.
You receive the COMPLETE structured investigation context including:

- FIR details
- suspects
- victims
- witnesses
- statements
- forensic findings
- evidence
- recovered items
- digital evidence
- call records
- financial activity
- linked cases
- nearby incidents
- timeline
- extracted entities
- investigation notes
- known contradictions
- derived investigative gaps
- already-generated leads

Your task is to generate 3-5 HIGH-VALUE investigative leads that move the case forward.

A lead is:
- ONE specific investigative observation
- backed by concrete evidence from the case
- with ONE clear operational next action

Think like a real investigation officer preparing actionable briefing notes for another officer.

━━━━━━━━━━━━━━━━━━━━
CORE INVESTIGATION OBJECTIVES
━━━━━━━━━━━━━━━━━━━━

Generate leads across MULTIPLE investigative dimensions — NOT only witnesses.

You should actively look for:

1. Suspect inconsistencies
2. Missing alibis
3. Contradictory witness statements
4. Timeline gaps
5. Digital evidence opportunities
6. Mobile/CNIC linkage opportunities
7. Vehicle tracing opportunities
8. Financial motives or transactions
9. Weapon tracing
10. CCTV opportunities
11. Linked-case similarities
12. Geographic crime clustering
13. Repeat offender patterns
14. Missing forensic work
15. Missing chain-of-custody verification
16. Unverified identities
17. Social/family/business relationships
18. Communication analysis
19. Escape route analysis
20. Behavioral escalation indicators

Do NOT over-focus on witnesses unless witnesses are actually central to the evidence.

━━━━━━━━━━━━━━━━━━━━
NON-NEGOTIABLE RULES
━━━━━━━━━━━━━━━━━━━━

1. NEVER invent:
   - CNICs
   - case IDs
   - suspect IDs
   - victim IDs
   - witness IDs
   - evidence IDs
   - finding IDs
   - note IDs
   - locations
   - vehicles
   - weapons
   - phone numbers

2. Every lead MUST reference at least ONE real record from the case context.

3. Every lead MUST explain:
   - WHAT triggered suspicion
   - WHY the lead matters
   - WHAT exact action should happen next

4. Prefer leads that directly address DERIVED GAPS.

5. If statements conflict:
   - explicitly identify the contradiction
   - name the conflicting records
   - propose re-interview or verification

6. NEVER generate duplicate investigative angles already present in:
   ALREADY-GENERATED LEADS

7. Avoid generic policing advice.
   BAD:
   - "Investigate suspect"
   - "Question witnesses"
   - "Look into evidence"

   GOOD:
   - "Re-interview WIT-2118-02 because their statement places SUS-2118-01 at Liaquat Road at 22:10 while tower dump data places the suspect mobile near F-10 at the same time."

8. Use the IDs INSIDE descriptions and next_step fields.

9. Output VALID JSON ONLY.
   No markdown.
   No explanation.
   No prose outside JSON.

10. Generate ONLY grounded leads.
    If insufficient evidence exists:
    {{"leads": [], "reason": "Insufficient grounded investigative material in case context."}}

11. If all meaningful angles are already exhausted:
    {{"leads": [], "reason": "All key investigative angles for this case have already been captured as leads. No new grounded leads available."}}

━━━━━━━━━━━━━━━━━━━━
LEAD PRIORITIZATION LOGIC
━━━━━━━━━━━━━━━━━━━━

Prioritize leads in this order:

HIGH PRIORITY:
- direct contradictions
- forensic inconsistencies
- suspect movement gaps
- linked-case matches
- weapon tracing
- digital evidence
- unverified alibis
- CCTV recovery opportunities
- escape route verification
- phone/CNIC linkage

MEDIUM PRIORITY:
- witness inconsistencies
- timeline clarification
- missing records
- social relationship mapping

LOW PRIORITY:
- administrative follow-ups
- weak correlations without supporting evidence

━━━━━━━━━━━━━━━━━━━━
ALLOWED LEAD TYPES
━━━━━━━━━━━━━━━━━━━━

type MUST be EXACTLY one of:
{" | ".join(ALLOWED_LEAD_TYPES)}

NEVER invent new lead types.
NEVER use semantically similar alternatives.
If uncertain, map the lead to the closest allowed type.

LEAD TYPE MAPPING EXAMPLES:

- CCTV retrieval → CCTV_REVIEW
- conflicting statements → STATEMENT_CONTRADICTION
- mobile analysis → DIGITAL_EVIDENCE
- missing alibi → ALIBI_VERIFICATION
- linked FIR similarity → LINKED_CASE_ANALYSIS
- firearm tracing → WEAPON_TRACE
- vehicle verification → VEHICLE_TRACE
- tower/location mismatch → LOCATION_VERIFICATION

━━━━━━━━━━━━━━━━━━━━
ALLOWED SEVERITIES
━━━━━━━━━━━━━━━━━━━━

severity MUST be EXACTLY one of:
{" | ".join(ALLOWED_SEVERITIES)}

Guidance:
- HIGH → likely to materially advance case
- MEDIUM → useful but not critical
- LOW → supporting or administrative lead

━━━━━━━━━━━━━━━━━━━━
OUTPUT SCHEMA
━━━━━━━━━━━━━━━━━━━━

Return EXACTLY this structure:

{{
  "leads": [
    {{
      "type": "<allowed_type>",
      "severity": "<allowed_severity>",
      "description": "...",
      "next_step": "...",
      "evidence_basis": "...",
      "cited_case_ids": [],
      "cited_cnics": [],
      "cited_suspect_ids": [],
      "cited_victim_ids": [],
      "cited_witness_ids": [],
      "cited_evidence_ids": [],
      "cited_finding_ids": [],
      "cited_note_ids": [],
      "addresses_gap": "",
      "confidence": 75
    }}
  ]
}}

━━━━━━━━━━━━━━━━━━━━
FIELD VALIDATION RULES
━━━━━━━━━━━━━━━━━━━━

description:
- must describe the actual investigative concern
- must mention relevant IDs
- must explain WHY it matters

next_step:
- MUST contain:
  action verb + specific record + operational reason

BAD:
"Investigate CASE-2026-2118"

GOOD:
"Obtain CCTV footage from Blue Area covering 21:00-22:00 to verify whether SUS-2118-01 used vehicle LEB-7821 during the post-incident escape window identified in witness statement WIT-2118-03."

evidence_basis:
- FULL explanatory sentence
- explain reasoning clearly
- connect records logically
- never only list IDs

confidence:
- integer 0-100 only
- 75 = strong lead
- 50 = moderate
- 25 = weak correlation

addresses_gap:
- plain gap code only
- no brackets
- no explanation

━━━━━━━━━━━━━━━━━━━━
QUALITY BAR
━━━━━━━━━━━━━━━━━━━━

Strong leads:
- connect multiple records together
- identify contradictions
- expose investigative blind spots
- propose verifiable actions
- are operationally executable

Weak leads:
- generic
- repetitive
- obvious without evidence
- unsupported speculation
- witness-only spam

Before returning:
- verify every ID exists in context
- verify lead type is allowed
- verify no duplicate angle
- verify each lead is operationally actionable
""")

def _fmt_date(d: Optional[date]) -> str:
    return d.strftime("%Y-%m-%d") if d else "—"


def _section_existing_leads(existing: List[str]) -> str:
    if not existing:
        return ""   # first run — nothing to skip
    out = [
        "=== ALREADY-GENERATED LEADS (DO NOT REPEAT THESE) ===",
        "The following leads have already been saved. Do not generate "
        "the same lead again, even with different wording:",
    ]
    for i, desc in enumerate(existing, 1):
        out.append(f"  {i}. {desc}")
    out.append(
        "If all obvious leads are already covered, look at the case "
        "from a DIFFERENT ANGLE — forensic gaps, entity mismatches, "
        "spatial patterns, timeline inconsistencies, or victim follow-up."
    )
    return "\n".join(out)

def _section_case(c: CaseContext) -> str:
    lines = [
        f"=== CASE {c.case_id} ===",
        f"Title: {c.case_title or '—'}",
        f"Type: {c.case_type or '—'}",
        f"Status: {c.case_status or '—'}",
        f"Priority: {c.priority or '—'}",
    ]
    incident = _fmt_date(c.incident_date)
    if c.incident_time:
        incident += f" at {c.incident_time}"
    loc_bits = [p for p in (c.area, c.city, c.province) if p]
    if loc_bits:
        incident += f", {', '.join(loc_bits)}"
    lines.append(f"Incident: {incident}")
    if c.days_since_incident is not None:
        lines.append(f"Days since incident: {c.days_since_incident}")
    if c.days_since_last_timeline_event is not None:
        lines.append(f"Days since last activity: {c.days_since_last_timeline_event}")
    if c.fir_number:
        lines.append(f"FIR: {c.fir_number}")
    if c.reporting_officer:
        lines.append(f"Reporting officer: {c.reporting_officer}")
    if c.assigned_investigator:
        lines.append(f"Assigned investigator: {c.assigned_investigator}")
    if c.police_station:
        lines.append(f"Police station: {c.police_station}")
    if c.description:
        lines.append(f"Description: {c.description.strip()[:600]}")
    if c.motive:
        lines.append(f"Motive (recorded): {c.motive}")
    if c.modus:
        lines.append(f"Modus operandi: {c.modus}")
    if c.weapon_used or c.weapon_description:
        lines.append(f"Weapon: {c.weapon_used or c.weapon_description}")
    if c.vehicle_used:
        lines.append(f"Vehicle used: {c.vehicle_used}")
    if c.ppc_sections:
        lines.append(f"PPC sections: {c.ppc_sections}")
    if c.num_suspects_expected:
        lines.append(f"Expected suspect count (from FIR): {c.num_suspects_expected}")
    lines.append(f"CCTV flag in FIR: {'yes' if c.cctv_flag else 'no'}")
    lines.append(f"Witness flag in FIR: {'yes' if c.witness_flag else 'no'}")

    # Crime-type subtype extras
    subtype_lines = []
    if c.cause_of_death:
        subtype_lines.append(f"Cause of death: {c.cause_of_death}")
    if c.body_location:
        subtype_lines.append(f"Body location: {c.body_location}")
    if c.time_of_death:
        subtype_lines.append(f"Time of death: {c.time_of_death}")
    if c.postmortem_done_subtype is not None:
        subtype_lines.append(f"Post-mortem completed: {c.postmortem_done_subtype}")
    if c.forensic_done_subtype is not None:
        subtype_lines.append(f"Forensic exam completed: {c.forensic_done_subtype}")
    if c.medical_exam:
        subtype_lines.append(f"Medical exam: {c.medical_exam}")
    if c.victim_counseling is not None:
        subtype_lines.append(f"Victim counseling: {c.victim_counseling}")
    if c.protection_order is not None:
        subtype_lines.append(f"Protection order issued: {c.protection_order}")
    if c.stolen_items:
        subtype_lines.append(f"Stolen items: {c.stolen_items}")
    if c.stolen_value:
        subtype_lines.append(f"Stolen value: PKR {c.stolen_value:,.0f}")
    if c.recovery_status:
        subtype_lines.append(f"Recovery status: {c.recovery_status}")
    if c.entry_point:
        subtype_lines.append(f"Entry point: {c.entry_point}")
    lines.extend(subtype_lines)

    return "\n".join(lines)


def _section_suspects(suspects: List[SuspectCtx]) -> str:
    if not suspects:
        return "=== SUSPECTS ON RECORD ===\n(none recorded)"
    out = ["=== SUSPECTS ON RECORD ==="]
    for s in suspects:
        head = f"[{s.suspect_id}] {s.name or 'Unknown'}"
        if s.cnic:
            head += f", CNIC {s.cnic}"
        if s.age:
            head += f", age {s.age}"
        out.append(head)
        if s.status:
            out.append(f"  Status: {s.status}")
        if s.relation:
            out.append(f"  Relation to case: {s.relation}")
        out.append(f"  Criminal record: {'Yes' if s.criminal_record else 'No'} · Arrested: {'Yes' if s.is_arrested else 'No'}")
        if s.affiliations:
            out.append(f"  Known affiliations: {s.affiliations}")
        if s.vehicle:
            out.append(f"  Vehicle: {s.vehicle}")
        if s.physical_description:
            desc = s.physical_description.strip()
            if len(desc) > 160:
                desc = desc[:157] + "…"
            out.append(f"  Physical description: {desc}")
        if s.reason:
            out.append(f"  Reason for suspicion: {s.reason}")
        if s.has_alibi and s.alibi:
            alibi = s.alibi.strip()
            if len(alibi) > 240:
                alibi = alibi[:237] + "…"
            out.append(f"  Alibi recorded: \"{alibi}\"")
        if s.days_detained is not None:
            out.append(f"  Days in detention: {s.days_detained}")
        if s.flags:
            out.append(f"  ⚑ FLAGS: {', '.join(s.flags)}")
    return "\n".join(out)


def _section_victims(victims: List[VictimCtx]) -> str:
    if not victims:
        return "=== VICTIMS ===\n(none recorded)"
    out = ["=== VICTIMS ==="]
    for v in victims:
        head = f"[{v.victim_id}] {v.name or 'Unknown'}"
        if v.cnic:
            head += f", CNIC {v.cnic}"
        if v.age:
            head += f", age {v.age}"
        out.append(head)
        if v.status:
            out.append(f"  Status: {v.status}")
        if v.primary_label:
            out.append(f"  Primary label: {v.primary_label}")
        if v.injury_type:
            out.append(f"  Injury type: {v.injury_type}")
        if v.nature_of_injuries:
            nat = v.nature_of_injuries.strip()
            if len(nat) > 200:
                nat = nat[:197] + "…"
            out.append(f"  Nature of injuries: {nat}")
        if v.cause_of_death:
            out.append(f"  Cause of death: {v.cause_of_death}")
        if v.postmortem_done is not None:
            out.append(f"  Post-mortem done: {v.postmortem_done}")
        if v.medical_report is not None:
            out.append(f"  Medical report on file: {v.medical_report}")
        if v.threat_level:
            out.append(f"  Threat level: {v.threat_level}")
        if v.protection_required:
            out.append(
                f"  Protection required: yes "
                f"(assigned: {v.protection_assigned or 'NOT ASSIGNED'})"
            )
        if v.next_follow_up:
            tag = f" (overdue by {abs(v.days_until_follow_up)} d)" if (v.days_until_follow_up is not None and v.days_until_follow_up < 0) else ""
            out.append(f"  Next follow-up: {_fmt_date(v.next_follow_up)}{tag}")
        if v.cooperative is False:
            out.append(f"  Cooperative: no")
        if v.flags:
            out.append(f"  ⚑ FLAGS: {', '.join(v.flags)}")
    return "\n".join(out)


def _section_witnesses(witnesses: List[WitnessCtx]) -> str:
    if not witnesses:
        return "=== WITNESSES ===\n(none recorded)"
    out = ["=== WITNESSES ==="]
    for w in witnesses:
        head = f"[{w.witness_id}] {'(anonymous)' if w.is_anonymous else (w.name or 'Unknown')}"
        if w.cnic:
            head += f", CNIC {w.cnic}"
        out.append(head)
        if w.witness_type:
            out.append(f"  Type: {w.witness_type}")
        if w.relation:
            out.append(f"  Relation: {w.relation}")
        if w.credibility:
            out.append(f"  Credibility: {w.credibility}")
        if w.statement_date:
            age_tag = f" ({w.days_since_statement} d ago)" if w.days_since_statement is not None else ""
            out.append(f"  Statement date: {_fmt_date(w.statement_date)}{age_tag}")
        if w.flags:
            out.append(f"  ⚑ FLAGS: {', '.join(w.flags)}")
    return "\n".join(out)


def _section_statements(statements: List[StatementCtx]) -> str:
    """Surface every statement together so the LLM can spot
    contradictions in a single pass. This is the section that produces
    "alibi vs witness account" leads."""
    if not statements:
        return "=== STATEMENTS ===\n(none recorded)"
    out = ["=== STATEMENTS (compare across sources for contradictions) ==="]
    for s in statements:
        text = s.text.strip()
        if len(text) > 320:
            text = text[:317] + "…"
        out.append(f"[{s.source_kind}] {s.source_id} — {s.source_name or 'unknown'}")
        out.append(f"  \"{text}\"")
    return "\n".join(out)


def _section_forensic(findings: List[ForensicFindingCtx]) -> str:
    if not findings:
        return "=== FORENSIC FINDINGS (on victims) ===\n(none recorded)"
    out = ["=== FORENSIC FINDINGS (on victims) ==="]
    for f in findings:
        text = f.finding_text.strip()
        if len(text) > 280:
            text = text[:277] + "…"
        age = f" ({f.days_since_recorded} d ago)" if f.days_since_recorded is not None else ""
        out.append(f"[{f.finding_id}] victim={f.victim_id}{age}")
        out.append(f"  {text}")
    return "\n".join(out)


def _section_evidence(
    evidence: List[EvidenceCtx],
    by_type: dict,
    analyzed: int,
    pending: int,
) -> str:
    if not evidence:
        return "=== EVIDENCE ON FILE ===\n(none recorded)"
    out = [
        f"=== EVIDENCE ON FILE ({analyzed} analyzed, {pending} pending) ==="
    ]
    if by_type:
        type_tally = ", ".join(f"{k}:{v}" for k, v in sorted(by_type.items()))
        out.append(f"Tally by type: {type_tally}")
    for e in evidence:
        head = f"[{e.evidence_id}] {e.type_label}"
        if e.date_collected:
            head += f" (collected {_fmt_date(e.date_collected)})"
        if e.collected_by:
            head += f" by {e.collected_by}"
        out.append(head)
        if e.description:
            desc = e.description.strip()
            if len(desc) > 200:
                desc = desc[:197] + "…"
            out.append(f"  {desc}")
        if e.flags:
            out.append(f"  ⚑ FLAGS: {', '.join(e.flags)}")
    return "\n".join(out)


def _section_entities(entities: List[EntityCtx]) -> str:
    if not entities:
        return "=== ENTITIES EXTRACTED FROM FIR ===\n(none)"
    out = ["=== ENTITIES EXTRACTED FROM FIR ==="]
    for e in entities:
        tag = "MATCHED" if e.matched else "UNMATCHED"
        flag = " [REVIEW]" if e.flagged_for_review else ""
        out.append(f"{e.type}: \"{e.value}\"  ({tag}, conf={int(e.confidence*100)}%){flag}")
    return "\n".join(out)


def _section_linked(linked: List[LinkedCaseCtx]) -> str:
    if not linked:
        return "=== LINKED CASES ===\n(none)"
    out = ["=== LINKED CASES (from deterministic linker) ==="]
    for l in linked:
        pct = int(round(l.similarity_score * 100)) if l.similarity_score <= 1.0 else int(l.similarity_score)
        relation_human = l.link_type.replace("_", " ").lower()
        head = f"[{l.case_id}] {l.title or '—'} ({pct}%, {relation_human})"
        out.append(head)
        if l.case_type:
            out.append(f"  Type: {l.case_type} · Status: {l.case_status or '—'}")
        if l.incident_date:
            out.append(f"  Incident: {_fmt_date(l.incident_date)}")
        if l.investigator_name:
            out.append(f"  Investigator: {l.investigator_name}")
        if l.explanation:
            out.append(f"  Link reason: {l.explanation}")
        if l.shared_suspect_cnics:
            out.append(f"  ⚑ Shared suspects (CNIC): {', '.join(l.shared_suspect_cnics)}")
    return "\n".join(out)


def _section_nearby(
    nearby: List[NearbyIncidentCtx],
    density: Optional[NearbyDensityCtx],
) -> str:
    if not nearby or not density:
        return "=== NEARBY INCIDENTS ===\n(spatial data not available or no neighbours)"
    out = [
        f"=== NEARBY INCIDENTS — {density.incident_count} cases "
        f"within {density.radius_km}km in last {density.period_days} days ==="
    ]
    if density.same_crime_type_count:
        out.append(f"  {density.same_crime_type_count} share THIS case's crime type")
    if density.by_crime_type:
        tally = ", ".join(f"{k}:{v}" for k, v in sorted(density.by_crime_type.items()))
        out.append(f"  Tally: {tally}")
    for n in nearby[:10]:        # cap rendering even though context allows up to 20
        line = (
            f"  [{n.case_id}] {n.title or '—'} "
            f"— {n.crime_type or '?'} · {n.distance_km}km away"
        )
        if n.days_ago is not None:
            line += f" · {n.days_ago}d ago"
        if n.case_status:
            line += f" · {n.case_status}"
        out.append(line)
    return "\n".join(out)


def _section_timeline(timeline: List[TimelineCtx]) -> str:
    if not timeline:
        return "=== INVESTIGATION TIMELINE ===\n(no events recorded)"
    out = ["=== INVESTIGATION TIMELINE (chronological, latest at bottom) ==="]
    for t in timeline:
        stamp = _fmt_date(t.event_date)
        if t.event_time:
            stamp += f" {t.event_time}"
        line = f"{stamp} — {t.title}"
        if t.officer_name:
            line += f" ({t.officer_name})"
        if t.description:
            desc = t.description.strip()
            if len(desc) > 150:
                desc = desc[:147] + "…"
            line += f": {desc}"
        out.append(line)
    return "\n".join(out)


def _section_notes(notes: List[NoteCtx]) -> str:
    if not notes:
        return "=== INVESTIGATOR NOTES ===\n(none)"
    out = ["=== INVESTIGATOR NOTES (their own hypotheses — respect their wording) ==="]
    for n in notes:
        head = f"[{n.note_id}] {n.title}"
        if n.category:
            head += f" ({n.category})"
        if n.officer_name:
            head += f" — {n.officer_name}"
        out.append(head)
        if n.detail:
            detail = n.detail.strip()
            if len(detail) > 280:
                detail = detail[:277] + "…"
            out.append(f"  {detail}")
    return "\n".join(out)


def _section_completeness(c: Optional[CompletenessCtx]) -> str:
    if not c:
        return "=== COMPLETENESS ===\n(no completeness report yet)"
    out = [
        f"=== COMPLETENESS (system-graded {c.score}/100) ==="
    ]
    if c.missing_fields:
        out.append(f"Missing fields: {', '.join(c.missing_fields[:12])}")
    if c.inconsistencies:
        out.append("System-flagged inconsistencies:")
        for inc in c.inconsistencies[:8]:
            out.append(f"  • {inc}")
    return "\n".join(out)


def _section_gaps(gaps: List[GapCtx]) -> str:
    """The most important section. Each gap is a ready-made lead waiting
    to be written up. The system prompt tells the LLM to lead with these."""
    if not gaps:
        return "=== DERIVED GAPS ===\n(no gaps detected — case is well-rounded)"
    out = [
        "=== DERIVED GAPS — pre-identified inconsistencies you should "
        "prioritise as leads ==="
    ]
    for g in gaps:
        refs = f"  refs: {', '.join(g.refs)}" if g.refs else ""
        out.append(f"• [{g.code}] {g.human}")
        if refs:
            out.append(refs)
    return "\n".join(out)

def build_user_prompt(ctx: CaseContext) -> str:
    """Stitch every section into the prompt body. Sections are ordered so
    the LLM sees STATIC FACTS first, then CONTRADICTIONS, then DERIVED
    GAPS at the end — which is what we want it to lead with."""

    sections = [
        _section_case(ctx),
        _section_suspects(ctx.suspects),
        _section_victims(ctx.victims),
        _section_witnesses(ctx.witnesses),
        _section_statements(ctx.statements),
        _section_forensic(ctx.forensic_findings),
        _section_evidence(
            ctx.evidence,
            ctx.evidence_by_type,
            ctx.evidence_analyzed_count,
            ctx.evidence_pending_count,
        ),
        # _section_entities(ctx.entities),
        _section_linked(ctx.linked),
        _section_nearby(ctx.nearby, ctx.nearby_density),
        # _section_timeline(ctx.timeline),
        _section_notes(ctx.notes),
        # _section_completeness(ctx.completeness),
        _section_gaps(ctx.gaps),
    ]

    existing_section = _section_existing_leads(ctx.existing_leads)
    if existing_section:
        sections.append(existing_section)

    body = "\n\n".join(s for s in sections if s)


    closing = (
        "\n\n---\n\n"
        "Generate 3-5 actionable leads in priority order. "
        "Lead with the items in DERIVED GAPS, then look for contradictions "
        "between STATEMENTS, then exploit LINKED CASES and NEARBY INCIDENTS. "
        "If all gaps are already covered by existing leads, explore: "
        "forensic evidence status, unmatched FIR entities, victim follow-up "
        "dates, evidence pending analysis, or spatial crime patterns. "
        "Cite only identifiers that appear above. Return JSON only."
        )
    return body + closing
