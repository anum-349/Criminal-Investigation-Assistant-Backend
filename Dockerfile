# ─────────────────────────────────────────────────────────────────────────────
# Main Backend — Dockerfile
#
# Builds the FastAPI backend (cases, auth, leads, users, etc.).
# This is the *CRUD* backend — the AI / FIR pipeline lives separately in
# fir-api/ and has its own image (much larger because of the ML deps).
#
# Build:  docker build -t cia-backend .
# Run:    docker run -p 8000:8000 cia-backend
# But normally you'll run it via `docker compose up` from the project root.
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System libs commonly needed:
#   • libpq-dev  → psycopg2 (Postgres driver)
#   • build-essential → compile any wheel that has no manylinux build
#   • curl → healthcheck
# If your requirements.txt doesn't include psycopg2-binary, swap libpq-dev for
# `libpq5` (runtime only) to save ~30 MB.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        curl \
        ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first so the layer caches when only source code changes.
COPY requirements.txt .
RUN pip install --upgrade pip wheel && \
    pip install -r requirements.txt

# Copy app source. .dockerignore keeps __pycache__, uploads, sqlite_data,
# fir-api, and other heavy/local-only stuff out of this image.
COPY . .

# Healthcheck — assumes you have a / or /health route on the main app.
# If you don't, comment this out or point it at any GET endpoint that's
# cheap and doesn't require auth.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/ || exit 1

EXPOSE 8000

# Production-style entrypoint. Override in docker-compose with `command:`
# for dev (uses --reload). Keep workers=1 by default because uvicorn workers
# don't share SQLAlchemy connection pools efficiently — scale via replicas
# behind a load balancer if you need more, not via --workers.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]