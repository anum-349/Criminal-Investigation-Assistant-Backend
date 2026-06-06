"""
Routes package — organized by domain for clean separation of concerns.

Domain structure:
  • /routes/cases/       - Case registration, retrieval, drafts, linking
  • /routes/investigations/ - FIR processing, entity extraction, lookups
  • /routes/leads/       - AI lead generation
  • /routes/admin/       - User mgmt, tickets, reports, analytics
  • /routes/notifications/ - WebSocket + REST notifications
  • /routes/account/     - Auth, profile, investigator dashboard
  • /routes/main_route.py & meta_routes.py - Dashboard, health checks
"""

from .cases import cases_router, case_links_router, drafts_router
from .investigations import fir_router, lookup_router
from .leads import leads_router
from .admin import admin_router, tickets_router, reports_router, analytics_router
from .notifications import notifications_ws_router, notifications_router
from .account import auth_router, investigator_router
from . import main_route

__all__ = [
    "cases_router",
    "case_links_router",
    "drafts_router",
    "fir_router",
    "lookup_router",
    "leads_router",
    "admin_router",
    "tickets_router",
    "reports_router",
    "analytics_router",
    "notifications_ws_router",
    "notifications_router",
    "auth_router",
    "investigator_router",
    "main_route",
]
