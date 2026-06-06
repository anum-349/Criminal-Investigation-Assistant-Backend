from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from models import (
    User, Case, Lead, TimelineEvent, Activity,
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
from services.db_retry import get_case_with_retry
from services.service_helper import _format_officer_name
from schemas.leads.case_lead_schema import LeadRow

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

@dataclass
class GenerationResult:
    case_id: str
    accepted_leads: List[LeadRow]
    rejected_count: int
    rejected_issues: List[List[str]]
    reason: Optional[str] = None
    duration_ms: int = 0
    model: str = ""

    context_depth_score: int = 0
    gap_count: int = 0
    raw_lead_count: int = 0

def _format_provenance_suffix(raw: Dict[str, Any]) -> str:
    """Build a readable provenance footer that gets appended to the
    Lead.description. The existing LeadDetailDialog renders the
    description verbatim, so this is how we surface the full citation
    set without adding new columns.

    Example output:

        ─── AI Lead Provenance ───
        Evidence basis: Suspect identified by witness; vehicle matches FIR entity.
        Cited suspects: SUS-2053-01 (CNIC 35201-1234567-1)
        Cited witnesses: WIT-2053-01
        Cited evidence: EVD-2053-02
        Addresses gap: SUSPECT_NO_ALIBI
    """
    lines = []

    basis = (raw.get("evidence_basis") or "").strip()
    if basis:
        lines.append(f"Evidence basis: {basis}")

    def _join(items: Optional[List[str]]) -> str:
        return ", ".join(items) if items else ""

    suspects = _join(raw.get("cited_suspect_ids"))
    cnics    = _join(raw.get("cited_cnics"))
    if suspects or cnics:
        bits = []
        if suspects:
            bits.append(suspects)
        if cnics:
            bits.append(f"CNICs: {cnics}")
        lines.append(f"Cited suspects: {' · '.join(bits)}")

    if raw.get("cited_victim_ids"):
        lines.append(f"Cited victims: {_join(raw['cited_victim_ids'])}")
    if raw.get("cited_witness_ids"):
        lines.append(f"Cited witnesses: {_join(raw['cited_witness_ids'])}")
    if raw.get("cited_evidence_ids"):
        lines.append(f"Cited evidence: {_join(raw['cited_evidence_ids'])}")
    if raw.get("cited_finding_ids"):
        lines.append(f"Cited forensic findings: {_join(raw['cited_finding_ids'])}")
    if raw.get("cited_case_ids"):
        lines.append(f"Cited cases: {_join(raw['cited_case_ids'])}")
    if raw.get("cited_note_ids"):
        lines.append(f"Cited notes: {_join(raw['cited_note_ids'])}")

    gap = (raw.get("addresses_gap") or "").strip()
    if gap:
        lines.append(f"Addresses gap: {gap}")

    if not lines:
        return ""
    return "\n\n─── AI Lead Provenance ───\n" + "\n".join(lines)


def _persist_ai_lead(
    db: Session,
    *,
    case: Case,
    user: User,
    request: Optional[Request],
    raw: Dict[str, Any],
    lead_index_offset: int = 0
) -> Lead:
    """Insert one AI-generated lead + the timeline + audit rows."""
    type_id = _lead_type_id(db, raw["type"])
    if type_id is None:
        raise HTTPException(
            status_code=400,
            detail=f"Lead type '{raw['type']}' missing from lkp_lead_types",
        )

    suspect_fk = None
    sids = raw.get("cited_suspect_ids") or []
    if sids:
        fk, _, _ = _resolve_suggested_suspect(db, case=case, name_or_id=sids[0])
        suspect_fk = fk

    similar_case_pk = None
    for cid in (raw.get("cited_case_ids") or []):
        if cid != case.case_id:
            similar_case_pk = _resolve_similar_case(db, case_id_str=cid)
            if similar_case_pk:
                break

    description = raw["description"].strip()
    description += _format_provenance_suffix(raw)

    lead = Lead(
        case_id_fk           = case.id,
        lead_id              = _next_lead_id(case.case_id, count=len(case.leads) + lead_index_offset),
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
    db.flush()

    now = datetime.now(UTC)
    db.add(TimelineEvent(
        case_id_fk    = case.id,
        event_id      = f"EVT-{int(now.timestamp() * 1000):X}-{secrets.token_hex(2).upper()}",
        event_source  = "SYSTEM",
        event_type_id = _timeline_event_type_id(db, "AI_LEAD_GENERATED"),
        title         = f"AI Lead Generated: {raw['type']}",
        description   = raw["description"][:200],
        officer_name  = _format_officer_name(user),
        severity_id   = _severity_id_by_label(db, raw.get("severity") or "Normal"),
        event_date    = now.date(),
        event_time    = now.strftime("%H:%M"),
        created_at    = now,
        editable      = False,
    ))

    db.add(Activity(
        title       = f"AI Lead {lead.lead_id} generated",
        description = raw["description"][:200],
        type        = "lead",
        case_id     = case.id,
        user_id     = user.id,
        created_at  = now,
    ))

    try:
        audit.log_event(
            db, user_id=user.id, action="CREATE", module="Case Management",
            detail=(
                f"AI lead {lead.lead_id} generated for case {case.case_id} "
                f"(type={raw['type']}, gap={raw.get('addresses_gap') or '-'})"
            ),
            target_type="lead", target_id=lead.lead_id, request=request,
        )
    except Exception:
        log.exception("Failed to write audit log for AI lead %s", lead.lead_id)

    return lead

def generate_leads(
    db: Session,
    *,
    user: User,
    case_id: str,
    request: Optional[Request] = None,
    strict_validation: bool = True,
) -> GenerationResult:
    """Full pipeline. Raises HTTPException on non-recoverable failures
    (route layer maps each to a clean HTTP status — see design § 9)."""

    get_case_with_retry(db, case_id)  # Ensure the case exists and is visible to the user.
    # 0. Liveness probe
    if not llm_client.is_available():
        log.warning("[lead-gen] Ollama not reachable. Returning 503.")
        raise HTTPException(
            status_code=503,
            detail="AI service unavailable. Start Ollama and try again.",
        )

    # 1. Context bundle
    ctx: CaseContext = build_case_context(db, user=user, case_id=case_id)
    log.info(
        "[lead-gen] case=%s context built: %d suspects, %d victims, %d witnesses, "
        "%d evidence, %d findings, %d entities, %d linked, %d nearby, %d gaps",
        ctx.case_id, len(ctx.suspects), len(ctx.victims), len(ctx.witnesses),
        len(ctx.evidence), len(ctx.forensic_findings), len(ctx.entities),
        len(ctx.linked), len(ctx.nearby), len(ctx.gaps),
    )

    # 2. Depth check
    depth = assess_context_depth(ctx)
    if not depth.is_deep_enough:
        log.info(
            "[lead-gen] case=%s context too thin (score=%d). Skipping LLM.",
            ctx.case_id, depth.score,
        )
        return GenerationResult(
            case_id            = ctx.case_id,
            accepted_leads     = [],
            rejected_count     = 0,
            rejected_issues    = [],
            reason             = (
                "Case has too little structured data for meaningful AI leads. "
                f"Add suspects, evidence, or witnesses first. "
                f"({'; '.join(depth.reasons)})"
            ),
            context_depth_score= depth.score,
            gap_count          = len(ctx.gaps),
        )

    # 3. Format prompt
    user_prompt = build_user_prompt(ctx)
    log.debug("[lead-gen] case=%s prompt length=%d chars", ctx.case_id, len(user_prompt))

    # 4. LLM call
    try:
        resp = llm_client.generate(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            max_tokens=600,
        )
    except LLMUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except LLMTimeout as e:
        raise HTTPException(status_code=504, detail=str(e))
    except (LLMBadResponse, LLMError) as e:
        log.exception("[lead-gen] LLM call failed for case %s", ctx.case_id)
        raise HTTPException(status_code=502, detail=f"AI service error: {e}")

    log.info(
        "[lead-gen] case=%s LLM returned in %dms (%d tokens)",
        ctx.case_id, resp.duration_ms, resp.eval_count,
    )

    # 5. Parse
    try:
        parsed = parse_llm_response(resp.text)
    except ValueError as e:
        log.error(
            "[lead-gen] case=%s unparseable JSON: %s. Raw: %s",
            ctx.case_id, e, resp.text[:500],
        )
        return GenerationResult(
            case_id            = ctx.case_id,
            accepted_leads     = [],
            rejected_count     = 0,
            rejected_issues    = [],
            reason             = "The AI model returned malformed output. Please try again.",
            duration_ms        = resp.duration_ms,
            model              = resp.model,
            context_depth_score= depth.score,
            gap_count          = len(ctx.gaps),
        )

    raw_leads = parsed.get("leads") or []
    if not raw_leads:
        return GenerationResult(
            case_id            = ctx.case_id,
            accepted_leads     = [],
            rejected_count     = 0,
            rejected_issues    = [],
            reason             = parsed.get("reason")
                                  or "The AI model didn't find any grounded leads for this case.",
            duration_ms        = resp.duration_ms,
            model              = resp.model,
            context_depth_score= depth.score,
            gap_count          = len(ctx.gaps),
        )

    # 6. Validate
    batch: BatchValidationReport = validate_batch(
        raw_leads, ctx, strict=strict_validation,
    )

    # 7. Persist accepted
    persisted_rows: List[LeadRow] = []
    case_row = (
        db.query(Case).filter(Case.id == ctx.case_internal_id).first()
    )

    for i, raw in enumerate(batch.accepted):
        try:
            lead = _persist_ai_lead(
                db, case=case_row, user=user, request=request, raw=raw,  lead_index_offset=i,
            )
            fresh = _resolve_lead(db, case=case_row, lead_id=lead.lead_id)
            persisted_rows.append(_row_from_lead(fresh))
        except HTTPException:
            db.rollback()
            raise
        except Exception:
            log.exception(
                "[lead-gen] Failed to persist accepted lead for case %s. Continuing.",
                ctx.case_id,
            )
            db.rollback()

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
        "[lead-gen] case=%s done. %d accepted, %d rejected, %d gaps addressed",
        ctx.case_id, len(persisted_rows), len(batch.rejected),
        sum(1 for l in batch.accepted if l.get("addresses_gap")),
    )

    return GenerationResult(
        case_id            = ctx.case_id,
        accepted_leads     = persisted_rows,
        rejected_count     = len(batch.rejected),
        rejected_issues    = [r.get("issues", []) for r in batch.rejected],
        reason             = None if persisted_rows else
                             "All generated leads failed validation. None were saved.",
        duration_ms        = resp.duration_ms,
        model              = resp.model,
        context_depth_score= depth.score,
        gap_count          = len(ctx.gaps),
        raw_lead_count     = len(raw_leads),
    )