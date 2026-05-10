from typing import List, Optional
from pydantic import BaseModel, ConfigDict


class LinkedCaseRow(BaseModel):
    """One row of the table. Field names match what the JSX reads directly."""
    model_config = ConfigDict(from_attributes=True)

    id:           str          # external case_id of the linked case (e.g. "C-10046")
    linkedCaseId: str          # same as `id` — kept for the Link.state.caseId prop
    title:        str
    investigator: str          # "Insp. A. Khan" or "—"
    registerDate: str          # MM/DD/YYYY (frontend currently shows it as-is)
    status:       str          # CaseStatus.label, e.g. "Open" / "Closed"
    relation:     str         

    linkType:        Optional[str]   = None   # raw CaseLink.link_type code
    similarityScore: Optional[float] = None   # 0.0 – 1.0 (or 0–100, see below)
    explanation:     Optional[str]   = None


class LinkedRelationOption(BaseModel):
    value: str            
    label: str            
    variant: str = "default"


class LinkedStatusOption(BaseModel):
    value: str
    label: str
    variant: str = "default"


class CaseLinkedCasesList(BaseModel):
    items:            List[LinkedCaseRow]
    total:            int
    page:             int
    page_size:        int
    relation_options: List[LinkedRelationOption]
    status_options:   List[LinkedStatusOption]