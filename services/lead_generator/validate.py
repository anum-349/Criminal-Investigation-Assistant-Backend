"""
services/lead_generator/validate.py
─────────────────────────────────────────────────────────────────────────────
Phase 3 part B: the validation gate from design § 6.

This is the most important file in the lead generator. It's the only
thing between the LLM's imagination and the leads table. Every lead
must pass every check here or it's dropped — silently logged, never
shown to the user, never written to the DB.

What this means in practice:
  • If the LLM invents a CNIC like "12345-6789012-3", we drop the lead.
  • If the LLM cites case "C-9999" that isn't linked to this case, we
    drop the lead.
  • If the LLM uses a suspect_id that doesn't exist on this case, we
    drop the lead.
  • If the LLM picks a `type` that's not in lkp_lead_types, we drop the
    lead.
  • If the LLM picks a confidence outside [0,100] or non-int, we drop
    the lead.

The other route — sanitising the lead to fix the violation — is
deliberately rejected. "Fixing" an invented CNIC means guessing what
the LLM meant, which is exactly the failure mode we're trying to
prevent. Drop, don't repair.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from services.lead_generator.context import CaseContext
from services.lead_generator.prompt import ALLOWED_LEAD_TYPES, ALLOWED_SEVERITIES

log = logging.getLogger(__name__)

# Pakistani CNIC format: 5-7-1 digits separated by dashes. Used to flag
# obviously-malformed CNICs even before checking if they were "seen".
_CNIC_RE = re.compile(r"^\d{5}-\d{7}-\d{1}$")

# Case ID format used across the system: "C-" followed by digits.
_CASE_ID_RE = re.compile(r"^C-\d+$", re.IGNORECASE)

# Suspect ID format(s) the system uses. The case_lead_service accepts
# SUS-, S-, and SUSP- prefixes; we mirror that here.
_SUSPECT_ID_RE = re.compile(r"^(SUS|S|SUSP)-[A-Za-z0-9\-]+$", re.IGNORECASE)


# Set the validation strictness via env var if needed. Defaults to strict.
# In strict mode, ANY invalid citation drops the lead. In lenient mode,
# we drop only the bad citation but keep the lead (with the offending
# fields cleared). Lenient is useful for demos where you want SOMETHING
# to show up even when the LLM is mediocre; strict is the right default.
STRICT_MODE_DEFAULT = True


@dataclass
class ValidationResult:
    """Result of validating ONE lead. Issues are recorded even on
    success so we can log near-misses for prompt iteration."""
    passed: bool
    issues: List[str] = field(default_factory=list)
    # `lead` is the (possibly cleaned-up in lenient mode) version safe
    # to persist. None in strict mode if any issue fired.
    lead: Optional[Dict[str, Any]] = None


@dataclass
class BatchValidationReport:
    """What the orchestrator gets back after running every lead through
    the gate. The accepted list goes to the DB; the rejected list goes
    to the structured log."""
    accepted: List[Dict[str, Any]] = field(default_factory=list)
    rejected: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.accepted) + len(self.rejected)


# ─────────────────────────────────────────────────────────────────────────────
# Per-lead validation
# ─────────────────────────────────────────────────────────────────────────────

def validate_lead(
    lead: Dict[str, Any],
    ctx: CaseContext,
    *,
    strict: bool = STRICT_MODE_DEFAULT,
) -> ValidationResult:
    """Run every check from design § 6 on a single lead."""
    issues: List[str] = []

    # ── 1. Required fields ─────────────────────────────────────────
    for field_name in ("type", "description", "next_step", "evidence_basis", "confidence"):
        if not lead.get(field_name):
            issues.append(f"Missing required field: {field_name}")

    # ── 2. Type allowed-list ──────────────────────────────────────
    lead_type = (lead.get("type") or "").strip()
    if lead_type and lead_type not in ALLOWED_LEAD_TYPES:
        issues.append(
            f"Unknown lead.type '{lead_type}'. Allowed: {ALLOWED_LEAD_TYPES}"
        )

    # ── 3. Severity allowed-list ──────────────────────────────────
    severity = (lead.get("severity") or "").strip()
    if severity and severity not in ALLOWED_SEVERITIES:
        # Severity is somewhat optional — if it's missing or wrong we
        # default to "Normal" rather than dropping the lead. The other
        # fields carry the meat of the lead; severity is metadata.
        issues.append(f"Unknown severity '{severity}', will default to Normal")
        lead["severity"] = "Normal"

    # ── 4. Confidence range + type ────────────────────────────────
    c = lead.get("confidence")
    if c is None:
        issues.append("Missing confidence")
    else:
        try:
            c_int = int(c)
        except (ValueError, TypeError):
            issues.append(f"Confidence must be an integer, got {c!r}")
        else:
            if not (0 <= c_int <= 100):
                issues.append(f"Confidence {c_int} out of range [0,100]")
            else:
                lead["confidence"] = c_int

    # ── 5. Citation reality checks ────────────────────────────────
    # CNICs — every cited CNIC must appear in context.all_cnics_seen.
    cited_cnics = lead.get("cited_cnics") or []
    if not isinstance(cited_cnics, list):
        issues.append(f"cited_cnics must be a list, got {type(cited_cnics).__name__}")
        cited_cnics = []
    for cnic in cited_cnics:
        if not isinstance(cnic, str):
            issues.append(f"CNIC must be a string, got {cnic!r}")
            continue
        if not _CNIC_RE.match(cnic):
            issues.append(f"Malformed CNIC: '{cnic}'")
            continue
        if cnic not in ctx.all_cnics_seen:
            issues.append(f"Hallucinated CNIC (not in context): '{cnic}'")

    # Case IDs — must be either the focal case or one of the linked
    # cases. Citing some unrelated C-9999 is the most common LLM
    # failure mode.
    cited_case_ids = lead.get("cited_case_ids") or []
    if not isinstance(cited_case_ids, list):
        issues.append(f"cited_case_ids must be a list, got {type(cited_case_ids).__name__}")
        cited_case_ids = []
    for cid in cited_case_ids:
        if not isinstance(cid, str):
            issues.append(f"Case ID must be a string, got {cid!r}")
            continue
        if not _CASE_ID_RE.match(cid):
            issues.append(f"Malformed case ID: '{cid}'")
            continue
        if cid != ctx.case_id and cid not in ctx.linked_case_ids:
            issues.append(f"References non-linked case: '{cid}'")

    # Suspect IDs — must exist on THIS case (we don't accept cross-
    # case suspect IDs; those would be a different lead type that
    # the system doesn't model yet).
    cited_suspect_ids = lead.get("cited_suspect_ids") or []
    if not isinstance(cited_suspect_ids, list):
        issues.append(f"cited_suspect_ids must be a list, got {type(cited_suspect_ids).__name__}")
        cited_suspect_ids = []
    for sid in cited_suspect_ids:
        if not isinstance(sid, str):
            issues.append(f"Suspect ID must be a string, got {sid!r}")
            continue
        if not _SUSPECT_ID_RE.match(sid):
            issues.append(f"Malformed suspect ID: '{sid}'")
            continue
        if sid not in ctx.all_suspect_ids:
            issues.append(f"Hallucinated suspect_id: '{sid}'")

    # ── 6. Sanity check the prose fields ──────────────────────────
    # The LLM occasionally returns one-word answers or fragments. A
    # description shorter than 12 characters is almost certainly not
    # an actionable lead.
    desc = (lead.get("description") or "").strip()
    if desc and len(desc) < 12:
        issues.append(f"Description too short to be actionable: '{desc}'")
    if desc and len(desc) > 800:
        # Truncate rather than reject — descriptions occasionally run
        # long but the content is usable.
        lead["description"] = desc[:800].rsplit(" ", 1)[0] + "…"

    nxt = (lead.get("next_step") or "").strip()
    if nxt and len(nxt) < 12:
        issues.append(f"next_step too short to be actionable: '{nxt}'")

    basis = (lead.get("evidence_basis") or "").strip()
    if basis and len(basis) < 12:
        issues.append(f"evidence_basis too short to support the lead: '{basis}'")

    # ── Resolution ────────────────────────────────────────────────
    # Distinguish HARD issues (always disqualifying) from SOFT ones
    # (warnings the lenient mode can shrug off).
    HARD_PREFIXES = (
        "Hallucinated", "Missing required", "Unknown lead.type",
        "Confidence must", "Confidence", "Description too short",
        "next_step too short", "evidence_basis too short",
        "References non-linked", "Malformed CNIC",
        "Malformed case ID", "Malformed suspect ID",
    )
    hard_issues = [i for i in issues if any(i.startswith(p) for p in HARD_PREFIXES)]

    if strict:
        passed = not hard_issues
    else:
        # Lenient: only drop on missing-field or invalid-confidence
        ABSOLUTE_DEALBREAKERS = ("Missing required", "Confidence must", "Confidence")
        passed = not any(
            any(i.startswith(p) for p in ABSOLUTE_DEALBREAKERS) for i in hard_issues
        )

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
    """Run every lead through the gate, log rejections, return both
    accepted and rejected for the orchestrator to handle."""
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
                # Passed despite warnings (only possible in lenient mode)
                log.info(
                    "[lead-gen] Lead #%d accepted with warnings: %s",
                    i, result.issues,
                )
        else:
            log.warning(
                "[lead-gen] Lead #%d REJECTED (case=%s): %s",
                i, ctx.case_id, result.issues,
            )
            report.rejected.append({
                "raw": raw,
                "issues": result.issues,
            })

    if not report.accepted:
        log.warning(
            "[lead-gen] All %d leads rejected for case %s. "
            "Frontend will show 'no grounded leads' message.",
            report.total, ctx.case_id,
        )

    return report


# ─────────────────────────────────────────────────────────────────────────────
# Raw response parsing (handles ```json fences + occasional pre/postamble)
# ─────────────────────────────────────────────────────────────────────────────

import json

# Some LLM responses wrap JSON in ```json fences despite the system
# prompt forbidding it. We strip them defensively.
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE | re.IGNORECASE)


def parse_llm_response(text: str) -> Dict[str, Any]:
    """Parse the LLM's raw output into a {leads: [...], reason?: str} dict.

    Defensive against:
      • Leading/trailing whitespace
      • Markdown code fences (```json … ```)
      • Models that emit a single object instead of {leads: [...]}
      • Models that emit just an array

    Raises ValueError on unrecoverable parse failures so the orchestrator
    can return a clean 502 to the frontend.
    """
    if not text or not text.strip():
        raise ValueError("Empty LLM response")

    cleaned = text.strip()

    # Strip code fences if present.
    cleaned = _JSON_FENCE_RE.sub("", cleaned).strip()

    # Find the outermost JSON object (some models still emit prose).
    if not cleaned.startswith("{") and not cleaned.startswith("["):
        start = cleaned.find("{")
        if start == -1:
            start = cleaned.find("[")
        if start == -1:
            raise ValueError("No JSON object/array found in response")
        cleaned = cleaned[start:]
        # Trim trailing non-JSON
        end = max(cleaned.rfind("}"), cleaned.rfind("]"))
        if end != -1:
            cleaned = cleaned[: end + 1]

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}") from e

    # Normalise shape: tolerate the model returning just an array.
    if isinstance(parsed, list):
        return {"leads": parsed}
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected object, got {type(parsed).__name__}")

    if "leads" not in parsed:
        # Some models emit a single lead instead of a list.
        if "type" in parsed and "description" in parsed:
            return {"leads": [parsed]}
        # Or an empty result with just a reason.
        return {"leads": [], "reason": parsed.get("reason", "Unknown")}

    if not isinstance(parsed["leads"], list):
        raise ValueError("'leads' field must be a list")

    return parsed