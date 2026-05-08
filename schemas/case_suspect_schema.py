from typing import List, Optional
from datetime import datetime, date
from pydantic import BaseModel, ConfigDict

class SuspectRow(BaseModel):
    """Field names match what CaseSuspects.jsx and the details dialog read."""
    model_config = ConfigDict(from_attributes=True)

    id:               str               # external suspect_id e.g. "S-10046"
    caseId:           str

    name:             Optional[str] = None
    cnic:             Optional[str] = None
    age:              Optional[int] = None
    gender:           Optional[str] = None
    contact:          Optional[str] = None
    address:          Optional[str] = None
    occupation:       Optional[str] = None

    status:           str
    relationToCase:   Optional[str] = None
    reason:           Optional[str] = None
    alibi:            Optional[str] = None
    arrested:         bool = False
    criminalRecord:   bool = False

    dateAdded:        Optional[str] = None       # YYYY-MM-DD
    statementDate:    Optional[str] = None       # filled in if you add the column

    physicalDescription: Optional[str] = None
    knownAffiliations:   Optional[str] = None
    arrivalMethod:       Optional[str] = None
    vehicleDescription:  Optional[str] = None
    notes:               Optional[str] = None

class CaseSuspectsList(BaseModel):
    items:           List[SuspectRow]
    total:           int
    page:            int
    page_size:       int
    status_options:  List[str]                # active SuspectStatus.label values


class UpdateSuspectRequest(BaseModel):
    """Mirror of one suspect entry from AddSuspectDialog (update mode).
    StepSuspects emits all fields, but only those listed here are persisted."""
    model_config = ConfigDict(extra="ignore")

    name:             Optional[str] = None
    cnic:             Optional[str] = None
    age:              Optional[int] = None
    gender:           Optional[str] = None
    contact:          Optional[str] = None
    address:          Optional[str] = None
    occupation:       Optional[str] = None

    status:           Optional[str] = None    # e.g. "Detained" — matches SuspectStatus.label
    relationToCase:   Optional[str] = None
    reason:           Optional[str] = None
    alibi:            Optional[str] = None
    arrested:         Optional[bool] = None
    criminalRecord:   Optional[bool] = None