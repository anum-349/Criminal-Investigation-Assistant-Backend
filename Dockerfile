# ─────────────────────────────────────────────────────────────────────────────
# FIR Processing Pipeline — Docker Image
# Base: Ubuntu 22.04 (gives Tesseract 4.x + Urdu language pack easily)
#
# Build:  docker build -t fir-pipeline .
# Run:    docker run -p 8000:8000 fir-pipeline
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim-bullseye

# ── System dependencies ───────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Tesseract OCR engine + language packs
    tesseract-ocr \
    tesseract-ocr-urd \
    tesseract-ocr-eng \
    # PDF → image conversion (poppler)
    poppler-utils \
    # Image processing
    libgl1 \
    libglib2.0-0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ── Python dependencies ───────────────────────────────────────────────────────
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Application code ──────────────────────────────────────────────────────────
COPY . .

# ── Run FastAPI with Uvicorn ──────────────────────────────────────────────────
EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]