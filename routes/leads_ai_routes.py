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
from services.lead_generator import (
    generate_leads,
    GenerationResult,
)
from schemas.case_lead_schema import LeadRow

log = logging.getLogger(__name__)

router = APIRouter()

class AIHealthResponse(BaseModel):
    """Returned by GET /leads/ai/health. The frontend uses this to
    decide whether to show the "Generate with AI" button at all."""
    available: bool
    model: str
    model_pulled: bool
    detail: str


class GenerateLeadsResponse(BaseModel):
    """Returned by POST /generate. Mirrors GenerationResult but in
    Pydantic so FastAPI's OpenAPI doc renders properly."""
    case_id:           str
    accepted_leads:    List[LeadRow]
    rejected_count:    int
    rejected_issues:   List[List[str]]
    reason:            Optional[str] = None
    duration_ms:       int = 0
    model:             str = ""

@router.get(
    "/leads/ai/health",
    response_model=AIHealthResponse,
    summary="Check whether the AI lead generator is ready",
)
def ai_health() -> AIHealthResponse:
    """Lightweight probe. Confirms Ollama is reachable AND that the
    configured model is pulled. Anything else (e.g. DB connectivity)
    is handled by the platform's /health endpoint.

    Returns 200 with the boolean rather than 503 so the frontend can
    read the state without try/catching every request."""
    available = llm_client.is_available()
    pulled    = llm_client.model_is_pulled() if available else False

    if not available:
        detail = (
            "Ollama is not reachable. Start it with `ollama serve`."
        )
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
    """Synchronous lead generation. Builds context, calls Llama,
    validates, persists, returns. Worst-case ~13s on a CPU laptop
    per § 8 of the design.

    HTTP error mapping (§ 9):
        400  case_id missing / malformed → handled by service layer
        403  user doesn't own the case   → handled by _resolve_case
        404  case not found              → handled by _resolve_case
        502  LLM returned garbage        → from generate_leads
        503  Ollama not reachable        → from generate_leads
        504  Generation timeout          → from generate_leads
        500  anything else               → caught by global handler
    """
    log.info(
        "[lead-gen-route] POST /generate case_id=%s user=%s",
        case_id, user.id,
    )
    result: GenerationResult = generate_leads(
        db, user=user, case_id=case_id, request=request,
    )
    return GenerateLeadsResponse(**asdict(result))


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
    """NDJSON-streaming variant of /generate. The actual LLM call is
    still blocking inside the orchestrator (Ollama's HTTP shape doesn't
    let us stream individual leads mid-generation cleanly — the whole
    JSON object comes out at once), but we emit the wrapping events
    progressively so the UI feels alive.

    For an FYP demo, this is honest: the UI shows progress, the model
    finishes the JSON, we validate + emit each lead as a separate line,
    then close the stream."""

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

        for lead in result.accepted_leads:
            yield json.dumps({
                "event": "lead",
                "lead":  lead.model_dump(mode="json"),
            }) + "\n"

        if result.rejected_count:
            yield json.dumps({
                "event":    "rejected",
                "count":    result.rejected_count,
                "issues":   result.rejected_issues,
            }) + "\n"

        yield json.dumps({
            "event":       "done",
            "accepted":    len(result.accepted_leads),
            "rejected":    result.rejected_count,
            "duration_ms": result.duration_ms,
            "reason":      result.reason,
        }) + "\n"

    return StreamingResponse(
        _emit(),
        media_type="application/x-ndjson",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )