from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict

class LeadSuspectRef(BaseModel):
    """Minimal info about a suggested suspect; rendered as a small block in
    the LeadDetailDialog. Either populated from the joined CaseSuspect row,
    or — when the user typed a free-text name — populated only with `name`
    and `basis`."""
    model_config = ConfigDict(from_attributes=True)

    name:       Optional[str] = None
    suspectId:  Optional[str] = None
    basis:      Optional[str] = None

class LeadRow(BaseModel):
    """One lead. Field names match what CaseLeads.jsx and LeadDetailDialog read."""
    model_config = ConfigDict(from_attributes=True)

    id:                str
    caseId:            str
    eventSource:       str                    # "ai" | "manual"
    type:              str
    description:       str
    severity:          str
    status:             str
    confidence:        Optional[float] = None  # 0-100 for AI; null for manual
    nextStep:          Optional[str] = None
    source:            Optional[str] = None    # "AI Analysis" | "Field Intelligence" | etc.
    officerName:       Optional[str] = None
    suggestedSuspect:  Optional[LeadSuspectRef] = None
    similarCaseId:     Optional[str] = None
    generatedAt:       datetime
    editable:          bool = False           # manual leads are editable; AI ones aren't
    dismissable:       bool = True

class LeadCounts(BaseModel):
    all:    int = 0
    ai:     int = 0
    manual: int = 0

class CaseLeadsList(BaseModel):
    items:           List[LeadRow]
    total:           int
    page:            int
    page_size:       int
    counts:          LeadCounts                # for the source filter pills
    type_options:    List[str]                # active LeadType labels for dropdown

class AddManualLeadRequest(BaseModel):
    """Mirrors the buildLead() output in AddLeadDialog."""
    model_config = ConfigDict(extra="ignore")

    type:             str
    severity:         str
    description:      str
    nextStep:         Optional[str] = None
    source:           Optional[str] = "Manual Entry"
    suggestedSuspect: Optional[str] = None     # free-text name OR existing SUS-XXXX id
    suspectBasis:     Optional[str] = None
    similarCaseId:    Optional[str] = None     # external case_id, e.g. "C-2040"
    weaponPattern:    Optional[str] = None
    locationArea:     Optional[str] = None
    confidence:       Optional[float] = None   # investigator-rated, 0-100
    status:           Optional[str] = "New"

class UpdateLeadStatusRequest(BaseModel):
    status: str                                # "New" | "Under Review" | etc.

class DeleteLeadResult(BaseModel):
    deleted_id: str