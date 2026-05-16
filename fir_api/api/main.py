"""
api/main.py
───────────
FastAPI entry point for the FIR processing service.

Endpoints:
    GET  /                — basic liveness
    GET  /health          — readiness (models loaded?) + per-model status
    POST /api/process-fir — main upload endpoint

Why a startup hook?
    Transformer models take 5-15 s to load cold. We pay that cost once at
    container start so every real request sees a warm model. Without this
    the first investigator to upload a FIR would wait 15 s and possibly
    time out.

CORS:
    Open to all origins in dev. In production this should be restricted to
    your frontend's URL via FIR_CORS_ORIGINS env var.
"""

from __future__ import annotations

import logging
import os
import tempfile
import traceback
from typing import Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from pipeline.model_loader import warmup, is_ready, models
from pipeline.orchestrator import run_pipeline

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
)
log = logging.getLogger("fir.api")


# ─────────────────────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="FIR Processing API",
    description=(
        "Upload a FIR document (PDF or image, English or Urdu). "
        "Returns a structured payload extracted by the AI pipeline: "
        "OCR → language detection → MarianMT translation → SBERT "
        "classification → hybrid regex+spaCy+mBERT entity extraction."
    ),
    version="2.0.0",
)

_origins_env = os.getenv("FIR_CORS_ORIGINS", "*")
_origins = [o.strip() for o in _origins_env.split(",")] if _origins_env != "*" else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".bmp"}
MAX_UPLOAD_BYTES   = int(os.getenv("FIR_MAX_UPLOAD_MB", "20")) * 1024 * 1024


# ─────────────────────────────────────────────────────────────────────────────
# Startup / shutdown
# ─────────────────────────────────────────────────────────────────────────────

@app.on_event("startup")
def _on_startup() -> None:
    log.info("Starting up — warming model cache…")
    status = warmup()
    log.info("Model status: %s", status)


@app.on_event("shutdown")
def _on_shutdown() -> None:
    log.info("Shutting down.")


# ─────────────────────────────────────────────────────────────────────────────
# Health / liveness
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/")
def root() -> dict:
    return {"service": "FIR Processing Pipeline", "status": "ok", "version": "2.0.0"}


@app.get("/health")
def health() -> dict:
    """Readiness probe. Returns 503 if critical models aren't loaded."""
    ready = is_ready()
    body = {
        "ready":  ready,
        "models": models.status,
    }
    if not ready:
        # 503 lets k8s / load balancers route traffic away during warmup
        return JSONResponse(status_code=503, content=body)
    return body


# ─────────────────────────────────────────────────────────────────────────────
# Main endpoint
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/process-fir")
async def process_fir(file: UploadFile = File(...)) -> JSONResponse:
    """Run the full pipeline on an uploaded FIR file.

    Errors:
        422 → bad input (unsupported type, unreadable, not a FIR)
        500 → unexpected server-side failure
        503 → models still warming up
    """
    if not is_ready():
        raise HTTPException(
            status_code=503,
            detail={
                "toast":   "info",
                "message": "AI models are still warming up. Please retry in a few seconds.",
            },
        )

    # ── Validate filename / extension ────────────────────────────────────────
    filename = file.filename or "upload"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=422,
            detail={
                "toast":   "error",
                "message": f"Unsupported file type '{ext}'. Allowed: PDF, PNG, JPG, JPEG, TIFF, BMP.",
            },
        )

    # ── Read upload (streamed so we don't blow memory on 50 MB scans) ────────
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail={
                "toast":   "error",
                "message": f"File too large ({len(raw) / 1024 / 1024:.1f} MB). "
                           f"Max is {MAX_UPLOAD_BYTES // 1024 // 1024} MB.",
            },
        )

    # ── Persist to a temp file (pdfplumber + pdf2image need a path) ──────────
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(raw)
        tmp_path = tmp.name

    try:
        result = run_pipeline(tmp_path, original_filename=filename)
        return JSONResponse(content=result)

    except ValueError as ve:
        # User-facing validation failure — toast it.
        log.info("Pipeline rejected upload '%s': %s", filename, ve)
        raise HTTPException(
            status_code=422,
            detail={"toast": "error", "message": str(ve)},
        )

    except Exception as e:
        # Anything else — log full trace and return a generic message.
        log.exception("Unexpected pipeline error on '%s'", filename)
        raise HTTPException(
            status_code=500,
            detail={
                "toast":   "error",
                "message": "Internal processing error. Please retry; if it persists, contact the admin.",
                # Don't leak internals in production — set FIR_DEBUG=1 to include
                "debug":   str(e) if os.getenv("FIR_DEBUG") == "1" else None,
            },
        )

    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass