"""
services/lead_generator/validate.py
─────────────────────────────────────────────────────────────────────────────
Phase 3b (v2): the validation gate. Same job as v1 — drop any lead that
cites an identifier the model invented — but expanded to cover every
new citation type the v2 prompt allows the LLM to use:

    cited_cnics         (Pakistani CNIC format, must be in context)
    cited_case_ids      (focal case + linked cases + nearby cases)
    cited_suspect_ids   (SUS-/S-/SUSP- prefix, must exist on this case)
    cited_victim_ids    (VIC-/V- prefix, must exist on this case)
    cited_witness_ids   (WIT-/W- prefix, must exist on this case)
    cited_evidence_ids  (EVD-/E- prefix, must exist on this case)
    cited_finding_ids   (FF- prefix; synthetic IDs from context.py)
    cited_note_ids      (NOTE- prefix; investigator-authored notes)

Plus a few v2-only sanity checks:
    • addresses_gap field, if set, must match a real gap code from context
    • description must reference at least one of the cited identifiers
      verbatim (anti-"generic lead" check)
    • next_step must contain an action verb (anti-vague-advice check)

Everything else (type allow-list, severity allow-list, confidence range,
description length, prose minimums, hard vs soft issues, lenient mode)
works exactly like v1.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from services.lead_generator.context import CaseContext
from services.lead_generator.prompt import ALLOWED_LEAD_TYPES, ALLOWED_SEVERITIES

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Identifier formats (defensive — the validator also checks SET membership
# after the format pass, so even a well-formed-but-invented ID is dropped)
# ─────────────────────────────────────────────────────────────────────────────

_CNIC_RE       = re.compile(r"^\d{5}-\d{7}-\d{1}$")
_CASE_ID_RE = re.compile(r"^(C-\d+|CASE-\d{4}-\d+)$", re.IGNORECASE)
_SUSPECT_ID_RE = re.compile(r"^(SUS|S|SUSP)-[A-Za-z0-9\-]+$",   re.IGNORECASE)
_VICTIM_ID_RE  = re.compile(r"^(VIC|V)-[A-Za-z0-9\-]+$",        re.IGNORECASE)
_WITNESS_ID_RE = re.compile(r"^(WIT|W)-[A-Za-z0-9\-]+$",        re.IGNORECASE)
_EVIDENCE_ID_RE= re.compile(r"^(EVD|E|EV)-[A-Za-z0-9\-]+$",     re.IGNORECASE)
_FINDING_ID_RE = re.compile(r"^FF-[A-Za-z0-9\-]+$",             re.IGNORECASE)
_NOTE_ID_RE    = re.compile(r"^NOTE-[A-Za-z0-9\-]+$",           re.IGNORECASE)

# Common imperative verbs the LLM should use in next_step. We don't
# require a specific verb — just SOME action-orientedness, which we
# detect with a permissive regex of strong action verbs.
_ACTION_VERBS = re.compile(
    r"\b(re-?interview|interview|investigate|verify|confirm|recover|"
    r"obtain|collect|cross[- ]?reference|review|escalate|request|"
    r"check|contact|trace|search|seize|arrest|detain|release|"
    r"compare|analyse|analyze|test|submit|file|forward|coordinate|"
    r"deploy|surveil|monitor|interrogate|reconcile|re-?examine|"
    r"summon|notify|brief|update|reopen|close|fingerprint|swab|"
    r"photograph|map|geo-?locate|process|inspect|secure|seal|"
    r"document|preserve|chain[- ]of[- ]custody|reconstruct|canvas|"
    r"canvass)\b",
    re.IGNORECASE,
)

STRICT_MODE_DEFAULT = True


@dataclass
class ValidationResult:
    passed: bool
    issues: List[str] = field(default_factory=list)
    lead: Optional[Dict[str, Any]] = None


@dataclass
class BatchValidationReport:
    accepted: List[Dict[str, Any]] = field(default_factory=list)
    rejected: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.accepted) + len(self.rejected)


# ─────────────────────────────────────────────────────────────────────────────
# Per-lead validation
# ─────────────────────────────────────────────────────────────────────────────

def _check_citation_list(
    *,
    field_name: str,
    raw: Any,
    fmt_re: re.Pattern,
    seen_set: set,
    seen_set_label: str,
    issues: List[str],
) -> List[str]:
    """Shared validator for any cited_* list. Returns the cleaned list
    (well-formed, in-context items only) so the orchestrator can use it.
    Issues are appended to the shared `issues` list."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        issues.append(f"{field_name} must be a list, got {type(raw).__name__}")
        return []

    cleaned: List[str] = []
    for item in raw:
        if not isinstance(item, str):
            issues.append(f"{field_name} entry must be a string, got {item!r}")
            continue
        normalised = item.strip()
        if not normalised:
            continue
        if not fmt_re.match(normalised):
            issues.append(f"Malformed {field_name} entry: '{normalised}'")
            continue
        # CNIC keeps its exact format; IDs uppercase
        check_value = normalised if field_name == "cited_cnics" else normalised.upper()
        if check_value not in seen_set and normalised not in seen_set:
            issues.append(f"Hallucinated {seen_set_label}: '{normalised}'")
            continue
        # Use the canonical form from the seen set when available
        cleaned.append(
            check_value if check_value in seen_set else normalised
        )
    return cleaned


def validate_lead(
    lead: Dict[str, Any],
    ctx: CaseContext,
    *,
    strict: bool = STRICT_MODE_DEFAULT,
) -> ValidationResult:
    """Run every v2 check on a single lead."""
    issues: List[str] = []

    # ── 1. Required fields ─────────────────────────────────────────
    for field_name in ("type", "description", "next_step", "evidence_basis", "confidence"):
        if not lead.get(field_name):
            issues.append(f"Missing required field: {field_name}")

    # ── 2. Type allow-list ────────────────────────────────────────
    lead_type = (lead.get("type") or "").strip()

    if not lead_type:
        lead["type"] = "Other"

    elif lead_type not in ALLOWED_LEAD_TYPES:
        log.info(
            "[lead-gen] Unknown lead.type '%s' — mapping to 'Other'",
            lead_type,
        )
        issues.append(
            f"Unknown lead.type '{lead_type}', mapped to 'Other'"
        )
        lead["type"] = "Other"

    # ── 3. Severity allow-list (soft — default rather than reject) ─
    severity = (lead.get("severity") or "").strip()
    if severity and severity not in ALLOWED_SEVERITIES:
        issues.append(f"Unknown severity '{severity}', defaulting to Normal")
        lead["severity"] = "Normal"

    # ── 4. Confidence ─────────────────────────────────────────────
    c = lead.get("confidence")
    if c is None or c == "":
        log.debug("confidence missing — defaulting to 50")
        lead["confidence"] = 50          # soft default, not a hard reject
    else:
        try:
            c_float = float(c)
            # Model sometimes returns 0.0–1.0 instead of 0–100 — normalise it
            if 0.0 < c_float <= 1.0:
                c_float = c_float * 100
            c_int = int(round(c_float))
            if not (0 <= c_int <= 100):
                log.debug("confidence %s out of range — clamping", c_int)
                c_int = max(0, min(100, c_int))
            lead["confidence"] = c_int
        except (ValueError, TypeError):
            log.debug("confidence unparseable %r — defaulting to 50", c)
            lead["confidence"] = 50
            
    # ── 5. Citation reality checks ────────────────────────────────
    lead["cited_cnics"] = _check_citation_list(
        field_name="cited_cnics",
        raw=lead.get("cited_cnics"),
        fmt_re=_CNIC_RE,
        seen_set=ctx.all_cnics_seen,
        seen_set_label="CNIC",
        issues=issues,
    )
    # Case IDs — the validator treats focal_case + linked + nearby as
    # "seen". ctx.all_case_ids_seen already contains all of them.
    lead["cited_case_ids"] = _check_citation_list(
        field_name="cited_case_ids",
        raw=lead.get("cited_case_ids"),
        fmt_re=_CASE_ID_RE,
        seen_set=ctx.all_case_ids_seen,
        seen_set_label="case ID",
        issues=issues,
    )
    lead["cited_suspect_ids"] = _check_citation_list(
        field_name="cited_suspect_ids",
        raw=lead.get("cited_suspect_ids"),
        fmt_re=_SUSPECT_ID_RE,
        seen_set=ctx.all_suspect_ids,
        seen_set_label="suspect_id",
        issues=issues,
    )
    lead["cited_victim_ids"] = _check_citation_list(
        field_name="cited_victim_ids",
        raw=lead.get("cited_victim_ids"),
        fmt_re=_VICTIM_ID_RE,
        seen_set=ctx.all_victim_ids,
        seen_set_label="victim_id",
        issues=issues,
    )
    lead["cited_witness_ids"] = _check_citation_list(
        field_name="cited_witness_ids",
        raw=lead.get("cited_witness_ids"),
        fmt_re=_WITNESS_ID_RE,
        seen_set=ctx.all_witness_ids,
        seen_set_label="witness_id",
        issues=issues,
    )
    lead["cited_evidence_ids"] = _check_citation_list(
        field_name="cited_evidence_ids",
        raw=lead.get("cited_evidence_ids"),
        fmt_re=_EVIDENCE_ID_RE,
        seen_set=ctx.all_evidence_ids,
        seen_set_label="evidence_id",
        issues=issues,
    )
    lead["cited_finding_ids"] = _check_citation_list(
        field_name="cited_finding_ids",
        raw=lead.get("cited_finding_ids"),
        fmt_re=_FINDING_ID_RE,
        seen_set=ctx.all_finding_ids,
        seen_set_label="finding_id",
        issues=issues,
    )
    lead["cited_note_ids"] = _check_citation_list(
        field_name="cited_note_ids",
        raw=lead.get("cited_note_ids"),
        fmt_re=_NOTE_ID_RE,
        seen_set=ctx.all_note_ids,
        seen_set_label="note_id",
        issues=issues,
    )

    # ── 6. addresses_gap field, if set, must match a real gap code ─
    gap = (lead.get("addresses_gap") or "").strip().strip("[]")  # strip brackets
    lead["addresses_gap"] = gap  # write back cleaned value
    if gap:
        valid_codes = {g.code for g in ctx.gaps}
        if gap not in valid_codes:
            issues.append(
                f"addresses_gap '{gap}' is not in the derived gap list. "
                f"Either leave empty or pick one of: {sorted(valid_codes)}"
            )
            # Clear it rather than reject — the rest of the lead may still be useful
            lead["addresses_gap"] = ""

    # ── 7. Prose-quality sanity checks ────────────────────────────
    desc = (lead.get("description") or "").strip()
    if desc and len(desc) < 12:
        issues.append(f"Description too short to be actionable: '{desc}'")
    if desc and len(desc) > 800:
        lead["description"] = desc[:800].rsplit(" ", 1)[0] + "…"

    nxt = (lead.get("next_step") or "").strip()
    if nxt and len(nxt) < 12:
        issues.append(f"next_step too short to be actionable: '{nxt}'")
    elif nxt and not _ACTION_VERBS.search(nxt):
        issues.append(
            f"next_step lacks a clear action verb: '{nxt[:80]}'"
        )
    elif nxt and len(nxt) < 40:
        # Soft warning — next_step is too short to be specific
        # e.g. "Investigate CASE-2026-2160" is only 30 chars and names no person/action
        issues.append(
            f"next_step is vague (under 40 chars) — add WHO does WHAT and WHY: '{nxt}'"
        )

    basis = (lead.get("evidence_basis") or "").strip()
    if basis and len(basis) < 8:
        issues.append(f"evidence_basis too short: '{basis}'")
    # ── 8. Anti-generic check ────────────────────────────────────
    # A lead that cites nothing AND mentions no identifier verbatim in
    # the description is almost certainly the "interview the witness"
    # kind of advice that's useless without context.
    any_citations = any(
        lead.get(k) for k in (
            "cited_cnics", "cited_case_ids", "cited_suspect_ids",
            "cited_victim_ids", "cited_witness_ids", "cited_evidence_ids",
            "cited_finding_ids", "cited_note_ids",
        )
    )
    if not any_citations:
        issues.append("Lead cites no identifiers (must reference at least one record)")

    # ── Resolution: hard vs soft ─────────────────────────────────
    HARD_PREFIXES = (
        "Hallucinated", "Missing required",
        "Confidence must", "Confidence", "Description too short",
        "next_step too short", "evidence_basis too short",
        "Lead cites no identifiers",
        "Malformed",
    )
    hard_issues = [i for i in issues if any(i.startswith(p) for p in HARD_PREFIXES)]

    if strict:
        passed = not hard_issues
    else:
        ABSOLUTE = ("Missing required", "Confidence must", "Confidence")
        passed = not any(any(i.startswith(p) for p in ABSOLUTE) for i in hard_issues)

    return ValidationResult(
        passed=passed,
        issues=issues,
        lead=lead if passed else None,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Batch validation
# ─────────────────────────────────────────────────────────────────────────────

def validate_batch(
    leads: List[Dict[str, Any]],
    ctx: CaseContext,
    *,
    strict: bool = STRICT_MODE_DEFAULT,
) -> BatchValidationReport:
    report = BatchValidationReport()
    for i, raw in enumerate(leads):
        if not isinstance(raw, dict):
            log.warning("[lead-gen] Lead #%d is not a dict: %r — dropping", i, type(raw))
            report.rejected.append({"raw": raw, "issues": ["not a JSON object"]})
            continue
        result = validate_lead(raw, ctx, strict=strict)
        if result.passed:
            report.accepted.append(result.lead)
            if result.issues:
                log.info(
                    "[lead-gen] Lead #%d accepted with warnings: %s",
                    i, result.issues,
                )
        else:
            log.warning(
                "[lead-gen] Lead #%d REJECTED (case=%s): %s",
                i, ctx.case_id, result.issues,
            )
            report.rejected.append({"raw": raw, "issues": result.issues})

    if not report.accepted:
        log.warning(
            "[lead-gen] All %d leads rejected for case %s. "
            "Frontend will show 'no grounded leads' message.",
            report.total, ctx.case_id,
        )
    return report


# ─────────────────────────────────────────────────────────────────────────────
# Raw response parsing
# ─────────────────────────────────────────────────────────────────────────────

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE | re.IGNORECASE)


def parse_llm_response(text: str) -> Dict[str, Any]:
    """Parse the LLM's raw text into a {leads: [...], reason?: str} dict.

    Defensive against:
      • Leading / trailing whitespace
      • Markdown code fences (```json … ```)
      • Models that emit a single object instead of {leads: [...]}
      • Models that emit a bare array
      • Models that wrap the JSON in prose ("Sure! Here you go:…")

    Raises ValueError on unrecoverable parse failures."""
    if not text or not text.strip():
        raise ValueError("Empty LLM response")

    cleaned = _JSON_FENCE_RE.sub("", text.strip()).strip()

    if not cleaned.startswith(("{", "[")):
        start = cleaned.find("{")
        if start == -1:
            start = cleaned.find("[")
        if start == -1:
            raise ValueError("No JSON object/array found in response")
        cleaned = cleaned[start:]
        end = max(cleaned.rfind("}"), cleaned.rfind("]"))
        if end != -1:
            cleaned = cleaned[: end + 1]

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}") from e

    if isinstance(parsed, list):
        return {"leads": parsed}
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected object, got {type(parsed).__name__}")

    if "leads" not in parsed:
        if "type" in parsed and "description" in parsed:
            return {"leads": [parsed]}
        return {"leads": [], "reason": parsed.get("reason", "Unknown")}

    if not isinstance(parsed["leads"], list):
        raise ValueError("'leads' field must be a list")

    return parsed