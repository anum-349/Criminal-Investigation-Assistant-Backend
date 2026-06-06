"""Cases routes — case registration, retrieval, draft management."""
from .case_routes import router as cases_router
from .case_links_generate import router as case_links_router
from .drafts_routes import router as drafts_router

__all__ = ["cases_router", "case_links_router", "drafts_router"]
