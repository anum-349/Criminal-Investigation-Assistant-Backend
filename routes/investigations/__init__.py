"""Investigation tools — FIR processing, entity extraction, lookups."""
from .fir_route import router as fir_router
from .lookup_routes import router as lookup_router

__all__ = ["fir_router", "lookup_router"]
