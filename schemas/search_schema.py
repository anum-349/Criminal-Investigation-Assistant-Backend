from typing import List, Optional
from pydantic import BaseModel, ConfigDict

class CaseSearchRow(BaseModel):
    """A row in the 'Cases' category."""
    model_config = ConfigDict(from_attributes=True)

    id:             str
    title:          str
    status:         str
    created:        str       # YYYY-MM-DD
    investigator:   str
    complainant:    Optional[str] = None
    fir_upload:     Optional[str] = None
    fir_id:         Optional[str] = None
    offense_type:   str
    location:       str


class SuspectSearchRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:          str          # external suspect_id e.g. "S-1001"
    name:        Optional[str] = None
    case_id:     str
    case_title:  str
    status:      str
    relation:    Optional[str] = None
    reason:      Optional[str] = None
    alibi:       Optional[str] = None


class VictimSearchRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:          str          # external victim_id
    name:        Optional[str] = None
    case_id:     str
    case_title:  str
    age:         Optional[int] = None
    gender:      Optional[str] = None
    contact:     Optional[str] = None
    status:      str
    injury_type: Optional[str] = None


class WitnessSearchRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:          str          # external witness_id
    name:        Optional[str] = None     # null if anonymous
    case_id:     str
    case_title:  str
    statement:   Optional[str] = None
    credibility: str
    contact:     Optional[str] = None


class LeadSearchRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:          str          # external lead_id
    case_id:     str
    case_title:  str
    description: str
    type:        str
    severity:    str
    status:      str
    date:        str          # YYYY-MM-DD


class LocationSearchRow(BaseModel):
    """Aggregated location row — one per (city, area)."""
    model_config = ConfigDict(from_attributes=True)

    id:            str        # synthetic, e.g. "LOC-001"
    area:          str
    crime_type:    str        # most-common crime type at this area
    cases:         int        # how many active cases at this area
    last_incident: str        # YYYY-MM-DD of most-recent case
    severity:      str
    case_ids:      List[str]


class SearchCounts(BaseModel):
    all:       int = 0
    cases:     int = 0
    suspects:  int = 0
    victims:   int = 0
    witnesses: int = 0
    leads:     int = 0
    locations: int = 0


class SearchResponse(BaseModel):
    query:      str
    counts:     SearchCounts
    cases:      List[CaseSearchRow]
    suspects:   List[SuspectSearchRow]
    victims:    List[VictimSearchRow]
    witnesses:  List[WitnessSearchRow]
    leads:      List[LeadSearchRow]
    locations:  List[LocationSearchRow]