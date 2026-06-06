"""
Services package — organized by domain for clean separation of concerns.

Domain structure:
  • services/cases/      - Case registration, detail, drafts, status updates
  • services/persons/    - Suspects, victims, witnesses, locations
  • services/leads/      - AI lead generation, completeness reports
  • services/admin/      - Users, tickets, reports, analytics, audit
  • services/fir/        - FIR processing, extraction, orchestration
  • services/search/     - Search, linking, semantic similarity
  • services/common/     - Helpers, notifications, database utilities

All services reuse model lookups, helper functions, and logging patterns.
No breaking changes — just cleaner organization.
"""

__all__ = []
