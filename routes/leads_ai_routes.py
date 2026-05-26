"""
routes/leads_ai_routes.py
─────────────────────────────────────────────────────────────────────────────
Phase 4b (v2): HTTP endpoints for the comprehensive AI lead generator.

Same three endpoints as v1, with added telemetry fields in the response:
  • context_depth_score  (0-15 — how rich is the case data?)
  • gap_count            (how many derived inconsistencies were spotted)
  • raw_lead_count       (how many leads the LLM emitted before validation)

These help the FYP report's evaluation chapter and give the frontend
something useful to display when "no leads" comes back ("we found 4
gaps but the model couldn't write grounded leads for them").
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from dependencies.auth import get_current_user
from db import get_db
from models import User

from services import llm_client
from services.lead_generator import generate_leads, GenerationResult
from schemas.case_lead_schema import LeadRow

log = logging.getLogger(__name__)

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# Response models
# ─────────────────────────────────────────────────────────────────────────────

class AIHealthResponse(BaseModel):
    available: bool
    model: str
    model_pulled: bool
    detail: str


class GenerateLeadsResponse(BaseModel):
    case_id:             str
    accepted_leads:      List[LeadRow]
    rejected_count:      int
    rejected_issues:     List[List[str]]
    reason:              Optional[str] = None
    duration_ms:         int = 0
    model:               str = ""
    context_depth_score: int = 0
    gap_count:           int = 0
    raw_lead_count:      int = 0


# ─────────────────────────────────────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/leads/ai/health",
    response_model=AIHealthResponse,
    summary="Check whether the AI lead generator is ready",
)
def ai_health() -> AIHealthResponse:
    available = llm_client.is_available()
    pulled    = llm_client.model_is_pulled() if available else False

    if not available:
        detail = "Ollama is not reachable. Start it with `ollama serve`."
    elif not pulled:
        detail = (
            f"Ollama is up but model '{llm_client.OLLAMA_MODEL}' is not "
            f"installed. Run `ollama pull {llm_client.OLLAMA_MODEL}`."
        )
    else:
        detail = "Ready."

    return AIHealthResponse(
        available    = available,
        model        = llm_client.OLLAMA_MODEL,
        model_pulled = pulled,
        detail       = detail,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Blocking generation
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/{case_id}/leads/generate",
    response_model=GenerateLeadsResponse,
    summary="Generate AI leads for a case (blocking)",
)
def generate_case_leads(
    case_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> GenerateLeadsResponse:
    """Synchronous lead generation. Returns the full GenerationResult.

    HTTP error mapping:
        400  case_id malformed       → handled by service layer
        403  user doesn't own case   → handled by _resolve_case
        404  case not found          → handled by _resolve_case
        502  LLM returned garbage    → from generate_leads
        503  Ollama not reachable    → from generate_leads
        504  Generation timeout      → from generate_leads
        500  anything else           → global handler
    """
    log.info(
        "[lead-gen-route] POST /generate case_id=%s user=%s",
        case_id, user.id,
    )
    result: GenerationResult = generate_leads(
        db, user=user, case_id=case_id, request=request,
    )
    return GenerateLeadsResponse(**asdict(result))


# ─────────────────────────────────────────────────────────────────────────────
# Streaming (NDJSON)
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/{case_id}/leads/generate/stream",
    summary="Generate AI leads for a case (streaming, NDJSON)",
)
def generate_case_leads_stream(
    case_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """NDJSON stream. One JSON object per line.

    Stream shape:
        {"event":"started", "case_id":"C-2053", "model":"…"}
        {"event":"context", "depth_score":9, "gap_count":4}
        {"event":"lead",    "lead":{…LeadRow}}
        {"event":"lead",    "lead":{…LeadRow}}
        {"event":"rejected","count":1, "issues":[[…]]}
        {"event":"done",    "accepted":2, "rejected":1, "duration_ms":5430,
                            "raw_lead_count":3, "gap_count":4, "reason":null}
    """

    def _emit():
        yield json.dumps({
            "event":   "started",
            "case_id": case_id,
            "model":   llm_client.OLLAMA_MODEL,
        }) + "\n"

        try:
            result = generate_leads(
                db, user=user, case_id=case_id, request=request,
            )
        except HTTPException as e:
            yield json.dumps({
                "event":  "error",
                "status": e.status_code,
                "detail": e.detail,
            }) + "\n"
            return
        except Exception as e:
            log.exception(
                "[lead-gen-stream] Unexpected error for case %s", case_id,
            )
            yield json.dumps({
                "event":  "error",
                "status": 500,
                "detail": f"Internal error: {e}",
            }) + "\n"
            return

        # Telemetry once context is built
        yield json.dumps({
            "event":        "context",
            "depth_score":  result.context_depth_score,
            "gap_count":    result.gap_count,
        }) + "\n"

        for lead in result.accepted_leads:
            yield json.dumps({
                "event": "lead",
                "lead":  lead.model_dump(mode="json"),
            }) + "\n"

        if result.rejected_count:
            yield json.dumps({
                "event":  "rejected",
                "count":  result.rejected_count,
                "issues": result.rejected_issues,
            }) + "\n"

        yield json.dumps({
            "event":           "done",
            "accepted":        len(result.accepted_leads),
            "rejected":        result.rejected_count,
            "duration_ms":     result.duration_ms,
            "raw_lead_count":  result.raw_lead_count,
            "gap_count":       result.gap_count,
            "reason":          result.reason,
        }) + "\n"

    return StreamingResponse(
        _emit(),
        media_type="application/x-ndjson",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )
