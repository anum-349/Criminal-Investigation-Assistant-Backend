from __future__ import annotations

import json
import logging
import os
import queue
import tempfile
import threading

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import StreamingResponse

from fir_api.pipeline.orchestrator import run_pipeline

logger = logging.getLogger("fir.routes")

router = APIRouter()

MAX_FILE_BYTES   = 20 * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".tiff"}

_SENTINEL = object()   


@router.post("/process-fir")
async def process_fir(file: UploadFile = File(...)):
    """
    Receive a scanned FIR, stream NDJSON progress events, then the final result.

    Stream shape (one JSON object per line):
        {"event":"step",  "step":"ocr",   "label":"OCR / text extraction…"}
        {"event":"step",  "step":"lang",  "label":"Detecting language…"}
        ...
        {"event":"done",  "success":true, "payload":{…}, "steps":{…}, "duration_ms":4200}
        -- OR on failure:
        {"event":"error", "detail":"Not a valid FIR."}
    """
    filename = file.filename or "upload"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return _ndjson_error(
            f"Unsupported file type '{ext}'. Upload a PDF or image (JPG/PNG/BMP)."
        )

    contents = await file.read()
    if len(contents) > MAX_FILE_BYTES:
        return _ndjson_error("File exceeds the 20 MB limit.")

    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name

    step_queue: queue.Queue = queue.Queue()

    def on_step(evt: dict):
        """Called by run_pipeline on each pipeline stage."""
        step_queue.put(evt)

    result_holder: dict = {}

    def _run():
        try:
            result_holder["result"] = run_pipeline(
                tmp_path,
                original_filename=filename,
                on_step=on_step,
            )
        except ValueError as e:
            result_holder["error"] = str(e)
        except Exception as e:
            logger.exception("FIR pipeline crashed")
            result_holder["error"] = "Internal pipeline error. Please retry."
        finally:
            step_queue.put(_SENTINEL)   

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    def _stream():
        try:
            while True:
                try:
                    item = step_queue.get(timeout=300)   # 5-min hard cap
                except queue.Empty:
                    yield json.dumps({"event": "heartbeat"}) + "\n"
                    continue  

                if item is _SENTINEL:
                    break

                yield json.dumps({"event": "step", **item}) + "\n"

            if "error" in result_holder:
                yield json.dumps({
                    "event":   "error",
                    "detail":  result_holder["error"],
                    "success": False,
                }) + "\n"
            else:
                result = result_holder["result"]
                yield json.dumps({"event": "done", **result}) + "\n"

        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    return StreamingResponse(
        _stream(),
        media_type="application/x-ndjson",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


def _ndjson_error(detail: str) -> StreamingResponse:
    """Return a single-line NDJSON error stream for early rejections."""
    def _gen():
        yield json.dumps({"event": "error", "detail": detail, "success": False}) + "\n"
    return StreamingResponse(_gen(), media_type="application/x-ndjson")