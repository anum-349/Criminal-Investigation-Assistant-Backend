# syntax=docker/dockerfile:1.6
# ─────────────────────────────────────────────────────────────────────────────
# FIR Processing Pipeline — Docker Image
#
# Two-stage build:
#   1. `builder`   installs Python deps and pre-downloads model weights
#   2. `runtime`   copies the built site-packages + weights into a clean image
#
# Why two stages?
#   • The HF / sentence-transformers download tooling is heavier than what we
#     need at runtime (pip cache, build deps). Splitting trims ~400 MB.
#   • Models are baked into the image: the container has zero external network
#     dependency at runtime — required by the offline-inference constraint of
#     the criminal-investigation domain.
#
# Build:  docker build -t fir-pipeline .
# Run:    docker run -p 8000:8000 fir-pipeline
# ─────────────────────────────────────────────────────────────────────────────


# =============================================================================
# Stage 1: builder
# =============================================================================
FROM python:3.11-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/opt/huggingface \
    TRANSFORMERS_CACHE=/opt/huggingface \
    HF_HUB_DISABLE_TELEMETRY=1

# Build dependencies. `build-essential` is needed for sentencepiece source build
# on some Python versions; `curl` lets us probe model URLs in case of mirror issues.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

# Install the CPU-only PyTorch wheel index first to keep the image small —
# the default index ships the CUDA-enabled torch which is ~2 GB.
RUN pip install --upgrade pip wheel setuptools && \
    pip install --extra-index-url https://download.pytorch.org/whl/cpu \
        -r requirements.txt

# ── Pre-download model weights ──────────────────────────────────────────────
# We run a tiny Python snippet that triggers HF to cache each model under
# /opt/huggingface. Doing this here means the runtime image has them ready —
# no first-request stall, no internet needed at runtime.
RUN python - <<'PY'
import os
os.environ["HF_HOME"] = "/opt/huggingface"

print(">>> Downloading MarianMT (ur->en)…")
from transformers import MarianMTModel, MarianTokenizer
MarianTokenizer.from_pretrained("Helsinki-NLP/opus-mt-ur-en")
MarianMTModel.from_pretrained("Helsinki-NLP/opus-mt-ur-en")

print(">>> Downloading multilingual SBERT…")
from sentence_transformers import SentenceTransformer
SentenceTransformer("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")

print(">>> Downloading Urdu NER (mBERT-Urdu)…")
# This one is optional — wrap in try/except so a model rename or 404
# doesn't break the entire build. The pipeline degrades gracefully.
try:
    from transformers import AutoTokenizer, AutoModelForTokenClassification
    URDU_NER = "mirfan899/uner-uner-mbert"
    AutoTokenizer.from_pretrained(URDU_NER)
    AutoModelForTokenClassification.from_pretrained(URDU_NER)
except Exception as e:
    print(f"!!! Urdu NER skipped: {e}")

print(">>> Downloading FastText language ID…")
# ftlangdetect downloads lid.176.bin on first call — trigger it now.
try:
    from ftlangdetect import detect
    detect("hello world")
except Exception as e:
    print(f"!!! ftlangdetect prefetch skipped: {e}")
PY

# spaCy model
RUN python -m spacy download en_core_web_sm


# =============================================================================
# Stage 2: runtime
# =============================================================================
FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/huggingface \
    TRANSFORMERS_CACHE=/opt/huggingface \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    TOKENIZERS_PARALLELISM=false \
    LOG_LEVEL=INFO

# System libraries needed at runtime:
#   • tesseract-ocr (+urd / +eng tessdata) for OCR
#   • poppler-utils for pdf2image (uses pdftoppm under the hood)
#   • libgl1, libglib2.0-0 for Pillow + OpenCV image ops
# We DO NOT install build-essential here — keeps the runtime image lean.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-urd \
        tesseract-ocr-eng \
        poppler-utils \
        libgl1 \
        libglib2.0-0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy Python packages + cached models from the builder stage.
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /opt/huggingface /opt/huggingface
# fasttext-langdetect caches its model in the user's home — copy that too.
COPY --from=builder /root/.cache /root/.cache

WORKDIR /app
COPY . .

# Healthcheck so Docker / k8s know when warmup finished.
HEALTHCHECK --interval=10s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://localhost:8001/health || exit 1

EXPOSE 8001

# In dev, set RELOAD=1 in docker-compose for hot-reload. Default (prod-style)
# uses 2 workers so a single slow request doesn't block the API.
ENV WORKERS=2
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port 8001 --workers ${WORKERS}"]