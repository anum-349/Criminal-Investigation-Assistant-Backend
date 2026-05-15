"""
FIR Processing Pipeline — FastAPI Entry Point
POST /api/process-fir  →  upload file, get structured payload back
"""

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import tempfile, os, traceback

from pipeline.orchestrator import run_pipeline

app = FastAPI(
    title="FIR Processing API",
    description="Upload a FIR document (PDF/image, Urdu or English) → get structured form payload",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".bmp"}

@app.get("/")
def health():
    return {"status": "ok", "service": "FIR Processing Pipeline"}

@app.post("/api/process-fir")
async def process_fir(file: UploadFile = File(...)):
    """
    Accept a FIR document, run the full pipeline, return structured payload.
    Errors (non-FIR docs, bad files) are returned as 422 with a toast-ready message.
    """
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=422,
            detail={
                "toast": "error",
                "message": f"Unsupported file type '{ext}'. Please upload PDF, PNG, JPG, or TIFF.",
            },
        )

    # Save upload to temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        result = run_pipeline(tmp_path, original_filename=file.filename)
        return JSONResponse(content=result)
    except ValueError as ve:
        # Pipeline validation errors → toast
        raise HTTPException(status_code=422, detail={"toast": "error", "message": str(ve)})
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail={"toast": "error", "message": "Internal processing error."})
    finally:
        os.unlink(tmp_path)