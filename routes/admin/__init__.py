"""Admin routes — user management, tickets, reports, analytics."""
from .admin_routes import router as admin_router
from .ticket_routes import router as tickets_router
from .report_routes import router as reports_router
from .analytics_routes import router as analytics_router

__all__ = ["admin_router", "tickets_router", "reports_router", "analytics_router"]
