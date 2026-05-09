from typing import List, Optional
from datetime import datetime, date
from pydantic import BaseModel, ConfigDict, Field

class VictimPersonal(BaseModel):
    name:       Optional[str] = None
    age:        Optional[int] = None
    contact:    Optional[str] = None
    gender:     Optional[str] = None
    occupation: Optional[str] = None
    cnic:       Optional[str] = None


class VictimIncident(BaseModel):
    residentialAddress: Optional[str] = None
    natureOfInjuries:   Optional[str] = None
    causeOfDeath:       Optional[str] = None
    declaredDead:       Optional[str] = None
    postmortemAutopsy:  Optional[str] = None


class VictimProtection(BaseModel):
    threatLevel:        Optional[str] = "—"
    protectionAssigned: Optional[str] = "No"
    notes:              Optional[str] = None


class VictimTimelineItem(BaseModel):
    """Mirrors the {date, text} shape the UI renders inside the timeline list."""
    date: Optional[str] = None       # free-text — the form takes any string
    text: Optional[str] = None


class VictimLegalItem(BaseModel):
    label: str
    done:  bool = False

class VictimSummaryRow(BaseModel):
    """Used for the "Victim #1 / IT Professional / Fatal" cards along the top
    of the page. Same shape as `victimsList` in the JSX."""
    model_config = ConfigDict(from_attributes=True)

    id:            str                # external victim_id, e.g. "V-10010"
    title:         str                # "Victim #1"
    role:          Optional[str] = None   # = person.occupation
    status:        str                # raw VictimStatus.label, e.g. "Deceased"
    statusVariant: str                # "fatal" | "noInjury" | "injured" | …

class VictimDetail(BaseModel):
    """Full body the page renders. Mirrors victim1Details verbatim."""
    model_config = ConfigDict(from_attributes=True)

    id:                str
    caseId:            str

    personal:          VictimPersonal
    incident:          VictimIncident
    injurySummary:     Optional[str] = None
    injuryRecordedBy:  Optional[str] = None

    forensic:          List[str]               = []
    timeline:          List[VictimTimelineItem] = []
    protection:        VictimProtection
    legal:             List[VictimLegalItem]   = []

    nextFollowUp:      Optional[str] = None    # YYYY-MM-DD or free-text
    caseType:          Optional[str] = None
    primaryLabel:      Optional[str] = None
    cooperative:       bool = True

    medicalReport:        bool = False
    postmortem:           bool = False
    protectionRequired:   bool = False

    statement:         Optional[str] = None


class CaseVictimsList(BaseModel):
    """GET /api/investigator/cases/{case_id}/victims response."""
    items:           List[VictimSummaryRow]
    total:           int
    status_options:  List[str]                # active VictimStatus.label values


class UpdateVictimRequest(BaseModel):
    """One victim entry from AddVictimDialog (update mode). StepVictims emits
    the entire victim object flat; we keep `extra='ignore'` so unknown
    UI-only fields (like `_extractedByAI`) don't break the validation."""
    model_config = ConfigDict(extra="ignore")

    # Person
    name:        Optional[str] = None
    cnic:        Optional[str] = None
    age:         Optional[int] = None
    gender:      Optional[str] = None
    contact:     Optional[str] = None
    address:     Optional[str] = None
    occupation:  Optional[str] = None

    # CaseVictim core
    status:             Optional[str] = None     # VictimStatus.label
    primaryLabel:       Optional[str] = None
    relation:           Optional[str] = None     # → relation_to_suspect
    injuryType:         Optional[str] = None
    natureOfInjuries:   Optional[str] = None
    causeOfDeath:       Optional[str] = None
    declaredDead:       Optional[str] = None
    postmortemAutopsy:  Optional[str] = None
    injurySummary:      Optional[str] = None
    injuryRecordedBy:   Optional[str] = None
    statement:          Optional[str] = None
    caseType:           Optional[str] = None     # informational only — UI-side label

    # Booleans
    medicalReport:       Optional[bool] = None
    postmortem:          Optional[bool] = None
    protectionRequired:  Optional[bool] = None
    cooperative:         Optional[bool] = None

    # Protection
    threatLevel:        Optional[str] = None    # Severity.label, e.g. "Low"
    protectionAssigned: Optional[str] = None    # text — "Yes", "No", or assignee name
    protectionNotes:    Optional[str] = Field(default=None, alias="notes")

    # Follow-up
    nextFollowUp:       Optional[str] = None    # YYYY-MM-DD (or empty)

    # Child-table replacements (full lists; backend wipes & re-inserts)
    forensic:           Optional[List[str]]               = None
    timeline:           Optional[List[VictimTimelineItem]] = None
    legal:              Optional[List[VictimLegalItem]]   = None