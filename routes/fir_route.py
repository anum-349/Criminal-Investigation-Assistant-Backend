"""
api/routes/fir_routes.py
────────────────────────
Single route: POST /api/process-fir

Called by StepFIRUpload.jsx with a multipart form containing one field:
    file  →  the scanned FIR (PDF / JPG / PNG / BMP)

Returns the exact JSON shape StepFIRUpload.jsx expects:
    { success, error, payload, steps, duration_ms }
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
import asyncio, json

# Import the orchestrator from wherever it lives in your project.
# Adjust the import path to match your actual folder structure:
#   fir_api/pipeline/orchestrator.py  →  from fir_api.pipeline.orchestrator import run_pipeline
#   fir_api/orchestrator.py           →  from fir_api.orchestrator import run_pipeline
from fir_api.pipeline.orchestrator import run_pipeline

logger = logging.getLogger("fir.routes")

router = APIRouter()

# File size limit — 20 MB matches what StepFIRUpload.jsx advertises
MAX_FILE_BYTES = 20 * 1024 * 1024

# Extensions the OCR step actually supports
ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".tiff"}


@router.post("/api/process-fir")
async def process_fir(file: UploadFile = File(...)):
    """
    Receive a scanned FIR, run the full pipeline, return structured payload.

    StepFIRUpload.jsx checks result.success — if False it shows an error toast.
    If True it calls onExtracted(result.payload) to populate the form.
    """

    # ── Validate file type ───────────────────────────────────────────────────
    filename = file.filename or "upload"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return JSONResponse(
            status_code=415,
            content={
                "success": False,
                "error":   f"Unsupported file type '{ext}'. Upload a PDF or image (JPG/PNG/BMP).",
                "payload": None,
            },
        )

    # ── Read + size-check ────────────────────────────────────────────────────
    contents = await file.read()
    if len(contents) > MAX_FILE_BYTES:
        return JSONResponse(
            status_code=413,
            content={
                "success": False,
                "error":   "File exceeds the 20 MB limit.",
                "payload": None,
            },
        )

    # ── Save to temp file (orchestrator needs a real path for fitz/EasyOCR) ──
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            tmp.write(contents)
            tmp_path = tmp.name

        # ── Run the full pipeline ────────────────────────────────────────────
        result = run_pipeline(tmp_path, original_filename=filename)

        # result already has the exact shape StepFIRUpload expects:
        # { success, error, payload, steps, duration_ms, timings_ms, models_ready }
        return JSONResponse(content=result)

    except ValueError as e:
        # run_pipeline raises ValueError for:
        #   • "not a FIR" (validation failed)
        #   • "could not extract text" (blank scan)
        logger.warning("FIR pipeline rejected document: %s", e)
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "error":   str(e),
                "payload": None,
            },
        )

    except Exception as e:
        logger.exception("FIR pipeline crashed")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error":   "Internal pipeline error. Please retry or contact support.",
                "payload": None,
            },
        )

    finally:
        # Always clean up the temp file
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

@router.get("/api/process-fir/stream")
async def process_fir_stream(file_path: str, filename: str = ""):
    """SSE endpoint — frontend EventSource connects here after uploading."""
    async def event_gen():
        steps = [
            ("ocr",        "OCR / text extraction…"),
            ("lang",       "Detecting language…"),
            ("regex",      "Regex pre-pass…"),
            ("groq",       "Groq / Llama extraction…"),
            ("validation", "Validating FIR…"),
            ("entities",   "Entity extraction (NLP)…"),
            ("payload",    "Building payload…"),
        ]
        # Each step is published by the orchestrator via a queue you inject;
        # simplest approach: run_pipeline in a thread, emit SSE as it logs.
        for key, label in steps:
            yield f"data: {json.dumps({'step': key, 'label': label})}\n\n"
            await asyncio.sleep(0)   # yield to event loop

    return StreamingResponse(event_gen(), media_type="text/event-stream")