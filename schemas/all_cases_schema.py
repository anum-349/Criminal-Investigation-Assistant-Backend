from typing import List, Optional, Literal
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict


# ─── Single case row ────────────────────────────────────────────────────────

class AllCasesRow(BaseModel):
    """One row in the All Cases table."""
    model_config = ConfigDict(from_attributes=True)

    id:             str                    # external case_id, e.g. "C-2053"
    title:          str
    crime_type:     str
    location:       str
    investigator:   str
    complainant:    Optional[str] = None
    fir_id:         Optional[str] = None
    status:         str
    severity:       str
    registered:     str                    # YYYY-MM-DD
    last_update:    str                    # YYYY-MM-DD


# ─── Tab counts ─────────────────────────────────────────────────────────────

class TabCounts(BaseModel):
    """Counts shown next to each status tab pill."""
    all:     int = 0
    Active:  int = 0
    Pending: int = 0
    Closed:  int = 0


# ─── Filter option lists ────────────────────────────────────────────────────

class FilterOptions(BaseModel):
    """
    Crime types and severities the dropdowns should display.
    Sourced from lkp_case_types / lkp_severities so the dropdowns always
    match the data, no hardcoding on the frontend.
    """
    crime_types: List[str]
    severities:  List[str]


# ─── Top-level response ─────────────────────────────────────────────────────

class AllCasesResponse(BaseModel):
    """Full payload for one All Cases page render."""
    items:           List[AllCasesRow]
    total:           int                 # filtered total (drives pagination)
    page:            int
    page_size:       int
    tab_counts:      TabCounts
    filter_options:  FilterOptions


# ─── Allowed sort fields (kept in sync with the frontend's SortIcon cols) ──

SortField = Literal[
    "title", "crimeType", "location",
    "investigator", "status", "registered", "lastUpdate",
]
SortDir = Literal["asc", "desc"]