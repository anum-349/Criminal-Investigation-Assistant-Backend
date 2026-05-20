"""
services/lead_generator/__init__.py
─────────────────────────────────────────────────────────────────────────────
Phase 4 part A: the orchestrator. This is the public entry point that
the route layer (api/routes/leads_ai.py) calls.

Flow (design § 4):
    1. Build context bundle      (services.lead_generator.context)
    2. Pre-flight depth check    (refuse if too thin)
    3. Format prompt             (services.lead_generator.prompt)
    4. Call Ollama               (services.llm_client)
    5. Parse JSON                (validate.parse_llm_response)
    6. Validate each lead        (validate.validate_batch)
    7. Persist via existing      (services.case_lead_service helpers,
       case_lead_service          but the model's `event_source` flag
       infrastructure)            stays AI so the UI knows the provenance)
    8. Return persisted rows

Every step that can fail does so with a clean exception that the route
layer maps to a sensible HTTP status — design § 9.
"""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from models import (
    User, Case, Lead, TimelineEvent,
    Activity,
)
from services import audit_service as audit
from services.case_lead_service import (
    EVENT_SOURCE_AI,
    _lead_type_id,
    _lead_status_id_by_label,
    _severity_id_by_label,
    _resolve_lead,
    _row_from_lead,
    _next_lead_id,
    _resolve_suggested_suspect,
    _resolve_similar_case,
    _timeline_event_type_id,
)
from services.service_helper import _format_officer_name
from schemas.case_lead_schema import LeadRow

from services import llm_client
from services.llm_client import (
    LLMError, LLMUnavailable, LLMTimeout, LLMBadResponse,
)
from services.lead_generator.context import (
    build_case_context, assess_context_depth, CaseContext,
)
from services.lead_generator.prompt import SYSTEM_PROMPT, build_user_prompt
from services.lead_generator.validate import (
    parse_llm_response, validate_batch, BatchValidationReport,
)

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Public result type — what the route returns to the frontend
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class GenerationResult:
    """Single payload returned by `generate_leads`. Shaped so the route
    can serialise it directly with FastAPI's default JSON encoder."""
    case_id: str
    accepted_leads: List[LeadRow]
    rejected_count: int                  # for the "X of Y leads accepted" toast
    rejected_issues: List[List[str]]     # parallel to rejected, for debug
    reason: Optional[str] = None         # populated when LLM returns 0 leads
    duration_ms: int = 0
    model: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Persistence — same path as case_lead_service.add_manual_lead, but with
# event_source flipped to AI and a few extra fields wired up
# ─────────────────────────────────────────────────────────────────────────────

def _persist_ai_lead(
    db: Session,
    *,
    case: Case,
    user: User,
    request: Optional[Request],
    raw: Dict[str, Any],
) -> Lead:
    """Insert one AI-generated lead and the matching timeline + audit
    rows. Mirrors the structure of add_manual_lead so audit + timeline
    history stays consistent across both flows."""
    type_id = _lead_type_id(db, raw["type"])
    if type_id is None:
        # Shouldn't happen — validator already checked the type — but
        # double-check because the DB has the final say. The validator
        # uses an in-memory list; if someone deactivates a LeadType in
        # the DB after server start, this guards the difference.
        raise HTTPException(
            status_code=400,
            detail=f"Lead type '{raw['type']}' missing from lkp_lead_types",
        )

    # Try to resolve cited_suspect_ids[0] → CaseSuspect FK. The first
    # citation is treated as primary; the rest stay in evidence_basis.
    suspect_fk = None
    cited = raw.get("cited_suspect_ids") or []
    if cited:
        fk, _, _ = _resolve_suggested_suspect(
            db, case=case, name_or_id=cited[0],
        )
        suspect_fk = fk

    # Pick the first cited case ID that isn't the focal case as
    # similar_case_id. The validator already confirmed it's linked.
    similar_case_pk = None
    for cid in (raw.get("cited_case_ids") or []):
        if cid != case.case_id:
            similar_case_pk = _resolve_similar_case(db, case_id_str=cid)
            if similar_case_pk:
                break

    # Combine description + evidence_basis so the LeadDetailDialog UI
    # surfaces the justification without needing a new column.
    description = raw["description"].strip()
    basis = raw.get("evidence_basis", "").strip()
    if basis:
        description = f"{description}\n\nEvidence basis: {basis}"

    lead = Lead(
        case_id_fk           = case.id,
        lead_id              = _next_lead_id(case.case_id, count=len(case.leads)),
        event_source         = EVENT_SOURCE_AI,
        type_id              = type_id,
        status_id            = _lead_status_id_by_label(db, "New"),
        severity_id          = _severity_id_by_label(db, raw.get("severity") or "Normal"),
        description          = description,
        confidence           = float(raw.get("confidence", 0)),
        next_step            = raw.get("next_step"),
        suggested_suspect_id = suspect_fk,
        similar_case_id      = similar_case_pk,
        created_by_user_id   = user.id,
    )
    db.add(lead)
    db.flush()  # gets us lead.id for the timeline + audit rows

    # Timeline event — what the user sees in the case timeline tab.
    now = datetime.now(UTC)
    db.add(TimelineEvent(
        case_id_fk    = case.id,
        event_id      = f"EVT-{int(now.timestamp() * 1000):X}-{secrets.token_hex(2).upper()}",
        event_source  = "SYSTEM",
        event_type_id = _timeline_event_type_id(db, "AI_LEAD_GENERATED"),
        title         = f"AI Lead Generated: {raw['type']}",
        description   = description[:200],
        officer_name  = _format_officer_name(user),
        severity_id   = _severity_id_by_label(db, raw.get("severity") or "Normal"),
        event_date    = now.date(),
        event_time    = now.strftime("%H:%M"),
        created_at    = now,
        editable      = False,
    ))

    # Activity feed (dashboard).
    db.add(Activity(
        title       = f"AI Lead {lead.lead_id} generated",
        description = description[:200],
        type        = "lead",
        case_id     = case.id,
        user_id     = user.id,
        created_at  = now,
    ))

    # Audit log.
    try:
        audit.log_event(
            db, user_id=user.id, action="CREATE", module="Case Management",
            detail=f"AI lead {lead.lead_id} generated for case {case.case_id} (type={raw['type']})",
            target_type="lead", target_id=lead.lead_id, request=request,
        )
    except Exception:
        # Audit failure shouldn't block lead persistence.
        log.exception("Failed to write audit log for AI lead %s", lead.lead_id)

    return lead


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def generate_leads(
    db: Session,
    *,
    user: User,
    case_id: str,
    request: Optional[Request] = None,
    strict_validation: bool = True,
) -> GenerationResult:
    """The one function the route calls. Does the full pipeline and
    returns the result. Raises HTTPException on any non-recoverable
    failure (maps to a clean response code on the wire)."""

    # ── 0. Liveness check ─────────────────────────────────────────
    # Cheap probe before we do any DB work. § 9 row 1.
    if not llm_client.is_available():
        log.warning("[lead-gen] Ollama not reachable. Returning 503.")
        raise HTTPException(
            status_code=503,
            detail="AI service unavailable. Start Ollama and try again.",
        )

    # ── 1. Build context bundle ───────────────────────────────────
    ctx: CaseContext = build_case_context(db, user=user, case_id=case_id)
    log.info(
        "[lead-gen] case=%s context built: %d suspects, %d evidence, %d entities, %d linked",
        ctx.case_id, len(ctx.suspects), len(ctx.evidence),
        len(ctx.entities), len(ctx.linked),
    )

    # ── 2. Depth check ───────────────────────────────────────────
    # § 9 row 6: don't waste an LLM call on a brand-new case with
    # nothing in it. Return a friendly explanation instead.
    depth = assess_context_depth(ctx)
    if not depth.is_deep_enough:
        log.info(
            "[lead-gen] case=%s context too thin (score=%d). Skipping LLM.",
            ctx.case_id, depth.score,
        )
        return GenerationResult(
            case_id        = ctx.case_id,
            accepted_leads = [],
            rejected_count = 0,
            rejected_issues= [],
            reason         = (
                "Case has too little structured data for meaningful AI leads. "
                f"Add suspects, evidence, or witnesses first. ({'; '.join(depth.reasons)})"
            ),
        )

    # ── 3. Format prompt ──────────────────────────────────────────
    user_prompt = build_user_prompt(ctx)
    log.debug("[lead-gen] case=%s prompt length=%d chars", ctx.case_id, len(user_prompt))

    # ── 4. Call Ollama ───────────────────────────────────────────
    try:
        resp = llm_client.generate(
            system_prompt = SYSTEM_PROMPT,
            user_prompt   = user_prompt,
        )
    except LLMUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except LLMTimeout as e:
        # § 9 row 5: timeout maps to 504, not 500. The frontend can
        # then offer a retry.
        raise HTTPException(status_code=504, detail=str(e))
    except (LLMBadResponse, LLMError) as e:
        log.exception("[lead-gen] LLM call failed for case %s", ctx.case_id)
        raise HTTPException(status_code=502, detail=f"AI service error: {e}")

    log.info(
        "[lead-gen] case=%s LLM returned in %dms (%d tokens)",
        ctx.case_id, resp.duration_ms, resp.eval_count,
    )

    # ── 5. Parse the JSON ────────────────────────────────────────
    # § 9 row 2: malformed JSON degrades to "no leads" rather than a
    # 500. We tried our best in parse_llm_response to recover; if it
    # still fails, the user gets a friendly empty result.
    try:
        parsed = parse_llm_response(resp.text)
    except ValueError as e:
        log.error(
            "[lead-gen] case=%s LLM returned unparseable JSON: %s. Raw: %s",
            ctx.case_id, e, resp.text[:500],
        )
        return GenerationResult(
            case_id        = ctx.case_id,
            accepted_leads = [],
            rejected_count = 0,
            rejected_issues= [],
            reason         = "The AI model returned malformed output. Please try again.",
            duration_ms    = resp.duration_ms,
            model          = resp.model,
        )

    raw_leads = parsed.get("leads") or []
    if not raw_leads:
        # § 9 row 4: 0 leads is a valid output — the LLM may have
        # decided the case is too thin even though our heuristic
        # said it was fine. Surface its reason if available.
        return GenerationResult(
            case_id        = ctx.case_id,
            accepted_leads = [],
            rejected_count = 0,
            rejected_issues= [],
            reason         = parsed.get("reason") or
                            "The AI model didn't find any grounded leads for this case.",
            duration_ms    = resp.duration_ms,
            model          = resp.model,
        )

    # ── 6. Validate each lead ────────────────────────────────────
    batch: BatchValidationReport = validate_batch(
        raw_leads, ctx, strict=strict_validation,
    )

    # ── 7. Persist accepted leads ────────────────────────────────
    persisted_rows: List[LeadRow] = []
    case_row = (
        db.query(Case)
        .filter(Case.id == ctx.case_internal_id)
        .first()
    )

    for raw in batch.accepted:
        try:
            lead = _persist_ai_lead(
                db, case=case_row, user=user, request=request, raw=raw,
            )
            # Rehydrate so the response shape matches what the list
            # endpoint returns — keeps the UI's LeadRow shape uniform.
            fresh = _resolve_lead(db, case=case_row, lead_id=lead.lead_id)
            persisted_rows.append(_row_from_lead(fresh))
        except HTTPException:
            # Re-raise: a 400 from _persist_ai_lead means the LeadType
            # vanished mid-flight, which is a real config error.
            db.rollback()
            raise
        except Exception:
            log.exception(
                "[lead-gen] Failed to persist accepted lead for case %s. "
                "Continuing with the rest.", ctx.case_id,
            )
            # Don't roll back the whole batch — if 2 of 4 leads
            # save successfully, that's still useful to the user.

    try:
        db.commit()
    except Exception:
        db.rollback()
        log.exception("[lead-gen] Final commit failed for case %s", ctx.case_id)
        raise HTTPException(
            status_code=500,
            detail="Failed to save generated leads. Please retry.",
        )

    log.info(
        "[lead-gen] case=%s done. %d accepted, %d rejected.",
        ctx.case_id, len(persisted_rows), len(batch.rejected),
    )

    return GenerationResult(
        case_id        = ctx.case_id,
        accepted_leads = persisted_rows,
        rejected_count = len(batch.rejected),
        rejected_issues= [r.get("issues", []) for r in batch.rejected],
        reason         = None if persisted_rows else
                         "All generated leads failed validation. None were saved.",
        duration_ms    = resp.duration_ms,
        model          = resp.model,
    )