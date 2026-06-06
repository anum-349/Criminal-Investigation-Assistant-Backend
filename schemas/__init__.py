"""
Schemas package — organized by domain with clean separation of concerns.

Domain structure:
  • schemas/cases/     - Case registration, detail, drafts, list
  • schemas/persons/   - Suspects, victims, witnesses, locations
  • schemas/leads/     - AI leads, completeness reports
  • schemas/admin/     - Users, tickets, reports, analytics, audit logs
  • schemas/common/    - Auth, search, common responses

Import paths:
  from schemas.cases.case_register_schema import CaseRegisterRequest
  from schemas.cases.case_detail_schema import CaseDetailResponse
  from schemas.cases.all_cases_schema import AllCasesResponse
  
  from schemas.persons.case_suspect_schema import SuspectInput, AddSuspectRequest
  from schemas.persons.case_victim_schema import VictimInput, AddVictimRequest
  from schemas.persons.case_witness_schema import WitnessInput, AddWitnessRequest
  from schemas.persons.case_location_schema import CaseLocationInput
  
  from schemas.leads.case_lead_schema import *
  from schemas.leads.predictive_ai_schema import *
  
  from schemas.admin.admin_schema import *
  from schemas.admin.ticket_schema import *
  from schemas.admin.analytics_schema import *
  from schemas.admin.report_schema import *
  
  from schemas.common.user_schema import UserLogin, UserRegister
  from schemas.common.response_schema import StandardResponse
  from schemas.common.search_schema import SearchResponse
  from schemas.common.investigator_dahboard_schema import InvestigatorDashboardResponse
  from schemas.common.case_evidence_schema import *
  from schemas.common.case_linked_schema import *
  from schemas.common.case_timeline_schema import *
"""

__all__ = []
