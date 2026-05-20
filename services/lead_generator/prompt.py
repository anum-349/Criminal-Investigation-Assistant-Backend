"""
services/lead_generator/prompt.py
─────────────────────────────────────────────────────────────────────────────
Phase 3 part A: build the system + user prompts for Llama.

The system prompt is invariant — same instructions for every call. The
user prompt is generated from a CaseContext per request. Both are pure
functions of their inputs (no DB access, no I/O), which makes them
trivial to unit-test.

Anything you change here is a behavioural change to the lead generator;
treat this file like configuration, not code.
"""
from __future__ import annotations

from datetime import date
from textwrap import dedent
from typing import List, Optional

from services.lead_generator.context import (
    CaseContext, SuspectCtx, VictimCtx, WitnessCtx,
    EvidenceCtx, EntityCtx, LinkedCaseCtx, TimelineCtx,
)

# ─────────────────────────────────────────────────────────────────────────────
# Allowed enum values (must match what the DB + validator accept)
# ─────────────────────────────────────────────────────────────────────────────

# These are the labels written by the seed migration into lkp_lead_types.
# Keep this list aligned with the seed. If we add a new lead type to the
# DB, also add it here so the LLM knows it's available.
ALLOWED_LEAD_TYPES = [
    "Suspect Lead",
    "Evidence",
    "Witness Statement",
    "Forensic",
    "Cross-reference",
    "Case Linkage",
    "Suspect Pattern",
    "Location Pattern",
    "Weapon Match",
]

ALLOWED_SEVERITIES = ["Low", "Normal", "High", "Critical"]


# ─────────────────────────────────────────────────────────────────────────────
# System prompt (design § 5.A)
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = dedent(f"""\
    You are an experienced criminal investigation analyst assisting Pakistani
    police investigators. Your job is to suggest concrete, evidence-grounded
    investigation leads for a given case.

    RULES (these are non-negotiable; the system will reject leads that violate them):

    1. Every CNIC, case ID, name, or fact you cite MUST appear verbatim in the
       provided case context. Do not invent identifiers.
    2. Every lead must reference at least one specific piece of evidence,
       entity, or linked case from the context. No generic advice
       ("interview witnesses") — always name the specific witness.
    3. Output VALID JSON only. No prose before or after the JSON block.
    4. Generate 3-5 leads, ordered by priority (highest first).
    5. If the case context is too thin to suggest grounded leads, return
       {{"leads": [], "reason": "<one-sentence explanation>"}} instead of
       making up leads.

    OUTPUT SCHEMA:

    {{
      "leads": [
        {{
          "type":            "<one of: {' | '.join(ALLOWED_LEAD_TYPES)}>",
          "severity":        "<one of: {' | '.join(ALLOWED_SEVERITIES)}>",
          "description":     "One sentence stating the lead.",
          "next_step":       "One sentence stating the specific action the investigator should take.",
          "evidence_basis":  "Why this lead is supported by the case data. Cite specific records.",
          "cited_case_ids":   ["C-2040", ...],
          "cited_cnics":      ["35201-1234567-1", ...],
          "cited_suspect_ids":["SUS-2053-01", ...],
          "confidence":      0-100
        }}
      ]
    }}

    Tone: write like an experienced investigator briefing a colleague —
    concrete, specific, and action-oriented. No filler, no hedging.
""")


# ─────────────────────────────────────────────────────────────────────────────
# User prompt builders (design § 5.B)
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_date(d: Optional[date]) -> str:
    return d.strftime("%Y-%m-%d") if d else "—"


def _section_case_header(c: CaseContext) -> str:
    lines = [
        f"=== CASE {c.case_id} ===",
        f"Title: {c.case_title or '—'}",
        f"Type: {c.case_type or '—'}",
        f"Status: {c.case_status or '—'}",
    ]
    incident = _fmt_date(c.incident_date)
    if c.incident_time:
        incident += f" at {c.incident_time}"
    location_bits = [p for p in (c.area, c.city, c.province) if p]
    if location_bits:
        incident += f", {', '.join(location_bits)}"
    lines.append(f"Incident: {incident}")
    if c.fir_number:
        lines.append(f"FIR: {c.fir_number}")
    if c.reporting_officer:
        lines.append(f"Reporting Officer: {c.reporting_officer}")
    if c.description:
        lines.append(f"Description: {c.description.strip()}")
    if c.motive:
        lines.append(f"Motive (recorded): {c.motive}")
    if c.modus:
        lines.append(f"Modus operandi (recorded): {c.modus}")
    if c.weapon_used:
        lines.append(f"Weapon: {c.weapon_used}")
    if c.ppc_sections:
        lines.append(f"PPC sections: {c.ppc_sections}")

    # Crime-type extras
    extras = []
    if c.cause_of_death:
        extras.append(f"Cause of death: {c.cause_of_death}")
    if c.body_location:
        extras.append(f"Body location: {c.body_location}")
    if c.time_of_death:
        extras.append(f"Time of death: {c.time_of_death}")
    if c.medical_exam:
        extras.append(f"Medical exam: {c.medical_exam}")
    if c.stolen_items:
        extras.append(f"Stolen items: {c.stolen_items}")
    if c.stolen_value:
        extras.append(f"Stolen value: PKR {c.stolen_value:,.0f}")
    lines.extend(extras)
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
        if s.relation:
            out.append(f"  Relation: {s.relation}")
        out.append(f"  Criminal record: {'Yes' if s.criminal_record else 'No'}")
        if s.affiliations:
            out.append(f"  Known affiliations: {s.affiliations}")
        if s.vehicle:
            out.append(f"  Vehicle: {s.vehicle}")
        if s.reason:
            out.append(f"  Reason: {s.reason}")
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
    return "\n".join(out)


def _section_witnesses(witnesses: List[WitnessCtx]) -> str:
    if not witnesses:
        return "=== WITNESSES ===\n(none recorded)"
    out = ["=== WITNESSES ==="]
    for w in witnesses:
        head = f"[{w.witness_id}] {w.name or '(anonymous)'}"
        if w.cnic:
            head += f", CNIC {w.cnic}"
        out.append(head)
        if w.credibility:
            out.append(f"  Credibility: {w.credibility}")
        if w.statement:
            # Trim long statements — full text inflates the prompt
            # without changing the lead-worthiness signal.
            trimmed = w.statement.strip()
            if len(trimmed) > 280:
                trimmed = trimmed[:277] + "…"
            out.append(f"  Statement excerpt: \"{trimmed}\"")
    return "\n".join(out)


def _section_evidence(evidence: List[EvidenceCtx]) -> str:
    if not evidence:
        return "=== EVIDENCE ON FILE ===\n(none recorded)"
    out = ["=== EVIDENCE ON FILE ==="]
    for e in evidence:
        line = f"[{e.evidence_id}] {e.type_label}"
        if e.description:
            desc = e.description.strip()
            if len(desc) > 160:
                desc = desc[:157] + "…"
            line += f" — {desc}"
        out.append(line)
    return "\n".join(out)


def _section_entities(entities: List[EntityCtx]) -> str:
    if not entities:
        return "=== ENTITIES EXTRACTED FROM FIR (not yet added to records) ===\n(none)"
    out = ["=== ENTITIES EXTRACTED FROM FIR (not yet added to records) ==="]
    for e in entities:
        line = f"{e.type}: \"{e.value}\""
        if e.notes:
            line += f"  ({e.notes})"
        out.append(line)
    return "\n".join(out)


def _section_linked(linked: List[LinkedCaseCtx]) -> str:
    if not linked:
        return "=== LINKED CASES (from automatic case-linking) ===\n(none)"
    out = ["=== LINKED CASES (from automatic case-linking) ==="]
    for l in linked:
        pct = int(round(l.similarity_score * 100)) if l.similarity_score <= 1.0 else int(l.similarity_score)
        # Convert SAME_SUSPECT → "same suspect" for readability
        relation_human = l.link_type.replace("_", " ").lower()
        head = f"[{l.case_id}] {l.title or '—'} ({pct}% confidence, {relation_human})"
        out.append(head)
        if l.explanation:
            # The case linker's `explanation` is already human-readable.
            out.append(f"  Link reason: {l.explanation}")
        out.append(f"  Investigator: {l.investigator_name}")
        if l.incident_date:
            out.append(f"  Incident: {_fmt_date(l.incident_date)}")
    return "\n".join(out)


def _section_timeline(timeline: List[TimelineCtx]) -> str:
    if not timeline:
        return "=== INVESTIGATION TIMELINE ===\n(no events recorded)"
    out = ["=== INVESTIGATION TIMELINE ==="]
    for t in timeline:
        stamp = _fmt_date(t.event_date)
        if t.event_time:
            stamp += f" {t.event_time}"
        line = f"{stamp} — {t.title}"
        if t.description:
            line += f": {t.description.strip()[:120]}"
        out.append(line)
    return "\n".join(out)


def build_user_prompt(ctx: CaseContext) -> str:
    """Stitch every section together into the prompt body. Layout mirrors
    design § 5.B exactly so the system prompt's schema makes sense to the
    LLM (it sees the same headings it's told to cite).

    Token budget: ~1500-3000 for a typical case, well within Llama's
    128k context. We don't trim anything else here — § 5.B caps each
    sub-list at sensible limits during context build.
    """
    sections = [
        _section_case_header(ctx),
        _section_suspects(ctx.suspects),
        _section_victims(ctx.victims),
        _section_witnesses(ctx.witnesses),
        _section_evidence(ctx.evidence),
        _section_entities(ctx.entities),
        _section_linked(ctx.linked),
        _section_timeline(ctx.timeline),
    ]

    body = "\n\n".join(sections)

    closing = (
        "\n\n---\n\n"
        "Generate 3-5 actionable leads. Remember: only cite identifiers "
        "that appear above. Return JSON only."
    )

    return body + closing