import os
import logging
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from db import Base, engine
from fir_api.pipeline.model_loader import warmup
from routes import (
    main_route,
    cases_router,
    case_links_router,
    drafts_router,
    fir_router,
    lookup_router,
    leads_router,
    admin_router,
    tickets_router,
    reports_router,
    analytics_router,
    notifications_ws_router,
    notifications_router,
    auth_router,
    investigator_router,
)

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
)
log = logging.getLogger("app")

if os.getenv("AUTO_CREATE_TABLES", "true").lower() == "true":
    Base.metadata.create_all(bind=engine)
    log.info("Schema ensured (AUTO_CREATE_TABLES=true)")

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(f"{app.title} v{app.version} starting up")
    log.info(f"Docs available at /docs")
    warmup()
    yield
    log.info(f"{app.title} shutting down")

app = FastAPI(
    title="AI-Powered Criminal Investigation Assistant",
    description=(
        "FYP backend for AI-assisted FIR processing, case linkage, lead "
        "generation, and investigation workflow management."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

UPLOADS_DIR = os.getenv("UPLOADS_DIR", "uploads")
os.makedirs(UPLOADS_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")

DEFAULT_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:3000"
]
origins_env = os.getenv("CORS_ORIGINS")
allowed_origins = (
    [o.strip() for o in origins_env.split(",") if o.strip()]
    if origins_env else DEFAULT_ORIGINS
)
log.info(f"CORS allowed origins: {allowed_origins}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],   # for file downloads
)

# ─────────────────────────────────────────────────────────────────────────────
# Route Registration (organized by domain)
# ─────────────────────────────────────────────────────────────────────────────

# Auth & Account
app.include_router(auth_router, prefix="/api/auth", tags=["Authentication"])
app.include_router(investigator_router, prefix="/api", tags=["Investigator Dashboard"])

# Main Dashboard & Health
app.include_router(main_route.router, prefix="/api", tags=["Dashboard"])

# Cases (registration, detail, drafts, links)
app.include_router(cases_router, prefix="/api/investigator/cases", tags=["Cases"])
app.include_router(drafts_router, prefix="/api/investigator/cases/case-drafts", tags=["Case Drafts"])
app.include_router(case_links_router, prefix="/api/investigator/cases", tags=["Case Links"])

# Investigations (FIR processing, lookups)
app.include_router(fir_router, prefix="/api", tags=["FIR Processing"])
app.include_router(lookup_router, prefix="/api/investigator/lookups", tags=["Lookups"])

# AI Leads
app.include_router(leads_router, prefix="/api/investigator/cases", tags=["AI Leads"])

# Notifications (WebSocket + REST)
app.include_router(notifications_ws_router, prefix="/api/notifications", tags=["Notifications"])
app.include_router(notifications_router, prefix="/api/notifications", tags=["Notifications"])

# Admin (users, tickets, reports, analytics)
app.include_router(admin_router, prefix="/api/admin", tags=["Admin"])
app.include_router(tickets_router, prefix="/api/tickets", tags=["Tickets"])
app.include_router(reports_router, prefix="/api/reports", tags=["Reports"])
app.include_router(analytics_router, prefix="/api/analytics", tags=["Analytics"])

# ─────────────────────────────────────────────────────────────────────────────
# Meta Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/", tags=["Meta"])
def root():
    return {
        "app":     "AI-Powered Criminal Investigation Assistant",
        "version": app.version,
        "docs":    "/docs",
        "health":  "/health",
    }


@app.get("/health", tags=["Meta"])
def health():
    from fir_api.pipeline.model_loader import models, is_ready
    return {
        "status": "ok" if is_ready() else "degraded",
        "models": models.status,
    }


@app.get("/health/db", tags=["Meta"])
def health_db():
    """Deeper check: confirms the DB is reachable. Use in monitoring."""
    from sqlalchemy import text
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "db": "reachable"}
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "db": "unreachable", "detail": str(e)},
        )

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    log.exception(f"Unhandled error on {request.method} {request.url.path}")
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "path":   request.url.path,
        },
    )
