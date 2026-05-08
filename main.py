import os
import logging
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from db import Base, engine
from routes import investigator_route, main_route, user_route

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

app.include_router(user_route.router, prefix="/api/auth", tags=["Authentication"])
app.include_router(main_route.router, prefix="/api",      tags=["Dashboard"])
app.include_router(investigator_route.router, prefix="/api",      tags=["Investigator Dashboard"])

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
    """Lightweight liveness probe. Doesn't touch the DB to stay fast."""
    return {"status": "ok"}


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
