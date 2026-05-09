from typing import List, Optional
from pydantic import BaseModel, ConfigDict


# ─── A single linked-case row ───────────────────────────────────────────────

class LinkedCaseRow(BaseModel):
    """One row of the table. Field names match what the JSX reads directly."""
    model_config = ConfigDict(from_attributes=True)

    id:           str          # external case_id of the linked case (e.g. "C-10046")
    linkedCaseId: str          # same as `id` — kept for the Link.state.caseId prop
    title:        str
    investigator: str          # "Insp. A. Khan" or "—"
    registerDate: str          # MM/DD/YYYY (frontend currently shows it as-is)
    status:       str          # CaseStatus.label, e.g. "Open" / "Closed"
    relation:     str          # human-readable, e.g. "Similar suspect description"

    # Useful extras (the UI ignores these today, but they'll let us show
    # confidence / explanation tooltips later without another endpoint)
    linkType:        Optional[str]   = None   # raw CaseLink.link_type code
    similarityScore: Optional[float] = None   # 0.0 – 1.0 (or 0–100, see below)
    explanation:     Optional[str]   = None


# ─── Filter dropdown options (so the UI can stay in sync with the DB) ──────

class LinkedRelationOption(BaseModel):
    value: str            # the slug used in the ?relation= query param
    label: str            # what the user sees in the dropdown
    variant: str = "default"


class LinkedStatusOption(BaseModel):
    value: str
    label: str
    variant: str = "default"


# ─── List response ──────────────────────────────────────────────────────────

class CaseLinkedCasesList(BaseModel):
    items:            List[LinkedCaseRow]
    total:            int
    page:             int
    page_size:        int
    relation_options: List[LinkedRelationOption]
    status_options:   List[LinkedStatusOption]