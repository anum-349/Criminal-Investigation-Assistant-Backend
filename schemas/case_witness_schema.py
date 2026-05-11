
from typing import List, Optional
from datetime import datetime, date
from pydantic import BaseModel, ConfigDict, Field


class WitnessRow(BaseModel):
    """Field names match what CaseWitnesses.jsx and the dialog read."""
    model_config = ConfigDict(from_attributes=True)

    # Core identifiers
    id:          str               # external witness_id, e.g. "W-10046"
    witnessId:   str               # alias of id (the dialog reads both)
    caseId:      str               # external case_id, e.g. "C-2053"

    # Person fields (joined; nulled when anonymous)
    name:        Optional[str] = None
    cnic:        Optional[str] = None
    age:         Optional[int] = None
    gender:      Optional[str] = None
    contact:     Optional[str] = None
    address:     Optional[str] = None

    # Classification
    witnessType:    Optional[str] = None     # WitnessType.label or None
    relationToCase: Optional[str] = None
    credibility:    Optional[str] = None     # WitnessCredibility.label

    # Status / timeline
    status:                Optional[str] = "Active"
    statementDate:         Optional[str] = None       # YYYY-MM-DD
    statementRecordedBy:   Optional[str] = None
    description:           Optional[str] = None       # the full statement
    dateAdded:             Optional[str] = None       # YYYY-MM-DD (created_at)

    # Privacy / safety flags
    anonymous:           bool = False
    protectionRequired:  bool = False
    cooperating:         bool = True
    
    photoUrl: Optional[str] = None

class CaseWitnessesList(BaseModel):
    items:           List[WitnessRow]
    total:           int
    page:            int
    page_size:       int

    status_options:        List[str]   # ["Active", "Pending", "Closed", …]
    credibility_options:   List[str]   # active WitnessCredibility.label values
    type_options:          List[str]   # active WitnessType.label values

class UpdateWitnessRequest(BaseModel):
    """One witness from AddWitnessDialog (update mode). Same flat shape that
    StepWitnesses emits in `mode='standalone'`. Empty strings mean
    'leave alone' so blank dialog fields don't accidentally null-out
    existing data — same convention used by case_suspect_service /
    case_victim_service."""
    model_config = ConfigDict(extra="ignore")

    # Person
    name:        Optional[str] = None
    cnic:        Optional[str] = None
    age:         Optional[int] = None
    gender:      Optional[str] = None
    contact:     Optional[str] = None
    address:     Optional[str] = None

    # Classification
    witnessType:    Optional[str] = None        # WitnessType.label
    relationToCase: Optional[str] = None
    credibility:    Optional[str] = None        # WitnessCredibility.label

    # Statement
    statementDate:        Optional[str] = None  # YYYY-MM-DD
    statementRecordedBy:  Optional[str] = None
    description:          Optional[str] = None

    # Status & flags
    status:              Optional[str] = None   # "Active" / "Pending" / "Closed" / "Hostile" / "Unavailable"
    anonymous:           Optional[bool] = None
    protectionRequired:  Optional[bool] = None
    cooperating:         Optional[bool] = None