FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ─── Runtime system dependencies (optimized for minimal footprint) ──────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        libmagic1 \
        poppler-utils \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender-dev \
        libgomp1 \
        curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Pre-downloaded torch wheels (local install, zero network risk)
COPY wheels/ /tmp/wheels/
COPY requirements.txt .

# ─── Install torch from local wheels first (most expensive) ────────────────
RUN pip install --upgrade pip setuptools wheel && \
    pip install /tmp/wheels/torch-2.4.1+cpu-cp311-cp311-linux_x86_64.whl \
                /tmp/wheels/torchvision-0.19.1+cpu-cp311-cp311-linux_x86_64.whl \
                --no-index && \
    rm -rf /tmp/wheels

# ─── Install all Python dependencies ──────────────────────────────────────
RUN pip install -r requirements.txt \
        --retries 5 \
        --timeout 120

# ─── Warm up spaCy + EasyOCR models offline (saves startup latency) ──────────
RUN python -m spacy download en_core_web_sm && \
    python -c "import easyocr; easyocr.Reader(['en', 'ur'], gpu=False)" 2>&1 || true

# Copy application code
COPY . .

# Create required directories
RUN mkdir -p /app/uploads /app/backups /app/sqlite_data

# Healthcheck
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=4 \
    CMD curl -fsS http://localhost:8000/health || \
        curl -fsS http://localhost:8000/ || exit 1

EXPOSE 8000

# Entrypoint
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
CMD ["/entrypoint.sh"]
