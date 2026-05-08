"""
schemas/case_suspects_schema.py
─────────────────────────────────────────────────────────────────────────────
Request + response shapes for the case-detail "Suspects" tab
(src/pages/investigator/case/CaseSuspects.jsx).

Endpoints these power:
  GET    /api/investigator/cases/{case_id}/suspects               (list + filter)
  GET    /api/investigator/cases/{case_id}/suspects/{sid}         (single)
  PATCH  /api/investigator/cases/{case_id}/suspects/{sid}         (update)

Notes on field coverage
───────────────────────
The CaseSuspect + Person models do NOT have columns for these UI-only fields:
   physicalDescription, knownAffiliations, arrivalMethod, vehicleDescription,
   notes, statementDate
The response includes them as `None`. If you decide to persist them later,
add columns to CaseSuspect (or a new related table) and update _row_from_suspect.
"""

from typing import List, Optional
from datetime import datetime, date
from pydantic import BaseModel, ConfigDict


# ─── A single suspect row (table + detail dialog) ──────────────────────────

class SuspectRow(BaseModel):
    """Field names match what CaseSuspects.jsx and the details dialog read."""
    model_config = ConfigDict(from_attributes=True)

    # Core identifiers
    id:               str               # external suspect_id e.g. "S-10046"
    caseId:           str

    # Person fields (joined)
    name:             Optional[str] = None
    cnic:             Optional[str] = None
    age:              Optional[int] = None
    gender:           Optional[str] = None
    contact:          Optional[str] = None
    address:          Optional[str] = None
    occupation:       Optional[str] = None

    # CaseSuspect fields
    status:           str
    relationToCase:   Optional[str] = None
    reason:           Optional[str] = None
    alibi:            Optional[str] = None
    arrested:         bool = False
    criminalRecord:   bool = False

    # Audit-ish
    dateAdded:        Optional[str] = None       # YYYY-MM-DD
    statementDate:    Optional[str] = None       # filled in if you add the column

    # Optional UI-only fields (not persisted today; reserved for later)
    physicalDescription: Optional[str] = None
    knownAffiliations:   Optional[str] = None
    arrivalMethod:       Optional[str] = None
    vehicleDescription:  Optional[str] = None
    notes:               Optional[str] = None


# ─── List response ──────────────────────────────────────────────────────────

class CaseSuspectsList(BaseModel):
    items:           List[SuspectRow]
    total:           int
    page:            int
    page_size:       int
    status_options:  List[str]                # active SuspectStatus.label values


# ─── PATCH body (Update dialog) ────────────────────────────────────────────

class UpdateSuspectRequest(BaseModel):
    """Mirror of one suspect entry from AddSuspectDialog (update mode).
    StepSuspects emits all fields, but only those listed here are persisted."""
    model_config = ConfigDict(extra="ignore")

    # Person (only if you want to update the underlying person row)
    name:             Optional[str] = None
    cnic:             Optional[str] = None
    age:              Optional[int] = None
    gender:           Optional[str] = None
    contact:          Optional[str] = None
    address:          Optional[str] = None
    occupation:       Optional[str] = None

    # Suspect specifics
    status:           Optional[str] = None    # e.g. "Detained" — matches SuspectStatus.label
    relationToCase:   Optional[str] = None
    reason:           Optional[str] = None
    alibi:            Optional[str] = None
    arrested:         Optional[bool] = None
    criminalRecord:   Optional[bool] = None