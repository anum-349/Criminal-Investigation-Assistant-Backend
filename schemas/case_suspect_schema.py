from typing import List, Optional
from datetime import datetime, date
from pydantic import BaseModel, ConfigDict
import re
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from typing import Optional, List
from datetime import date

CNIC_RE  = re.compile(r'^\d{5}-\d{7}-\d$')
PHONE_RE = re.compile(r'^(\+92|0)[-\s]?\d{3}[-\s]?\d{7}$')

class SuspectInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    suspectId:           Optional[str]  = None
    name:                Optional[str]  = None
    cnic:                Optional[str]  = None
    age:                 Optional[int]  = None
    gender:              Optional[str]  = None
    contact:             Optional[str]  = None
    address:             Optional[str]  = None
    occupation:          Optional[str]  = None
    status:              Optional[str]  = None
    relationToCase:      Optional[str]  = None
    reason:              Optional[str]  = None
    alibi:               Optional[str]  = None
    physicalDescription: Optional[str]  = None
    knownAffiliations:   Optional[str]  = None
    arrivalMethod:       Optional[str]  = None
    vehicleDescription:  Optional[str]  = None
    notes:               Optional[str]  = None
    criminalRecord:      Optional[bool] = False
    arrested:            Optional[bool] = False

    @field_validator("cnic")
    @classmethod
    def cnic_format(cls, v):
        if v and not CNIC_RE.match(v.strip()):
            raise ValueError("CNIC must be in format 00000-0000000-0")
        return v

    @field_validator("contact")
    @classmethod
    def contact_format(cls, v):
        if v and not PHONE_RE.match(v.strip()):
            raise ValueError("Contact must be a valid Pakistani number e.g. 0300-1234567")
        return v

    @field_validator("age")
    @classmethod
    def age_range(cls, v):
        if v is not None and v < 7:
            raise ValueError("Age must be at least 7")
        if v is not None and v > 120:
            raise ValueError("Age must be 120 or less")
        return v

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v):
        if v is not None and v.strip() == "":
            raise ValueError("Name cannot be blank if provided")
        return v
 

class AddSuspectRequest(BaseModel):
    suspects: List[SuspectInput]

class SuspectRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:                  str
    caseId:              str
    name:                Optional[str] = None
    cnic:                Optional[str] = None
    age:                 Optional[int] = None
    gender:              Optional[str] = None
    contact:             Optional[str] = None
    address:             Optional[str] = None
    occupation:          Optional[str] = None
    status:              str
    relationToCase:      Optional[str] = None
    reason:              Optional[str] = None
    alibi:               Optional[str] = None
    arrested:            bool = False
    criminalRecord:      bool = False
    dateAdded:           Optional[str] = None
    statementDate:       Optional[str] = None
    physicalDescription: Optional[str] = None
    knownAffiliations:   Optional[str] = None
    arrivalMethod:       Optional[str] = None
    vehicleDescription:  Optional[str] = None
    notes:               Optional[str] = None
    photoUrl:            Optional[str] = None    # ← added


class UpdateSuspectRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name:                Optional[str]  = None
    cnic:                Optional[str]  = None
    age:                 Optional[int]  = None
    gender:              Optional[str]  = None
    contact:             Optional[str]  = None
    address:             Optional[str]  = None
    occupation:          Optional[str]  = None
    status:              Optional[str]  = None
    relationToCase:      Optional[str]  = None
    reason:              Optional[str]  = None
    alibi:               Optional[str]  = None
    physicalDescription: Optional[str]  = None
    knownAffiliations:   Optional[str]  = None
    arrivalMethod:       Optional[str]  = None
    vehicleDescription:  Optional[str]  = None
    notes:               Optional[str]  = None
    arrested:            Optional[bool] = None
    criminalRecord:      Optional[bool] = None

    # reuse same validators
    @field_validator("cnic")
    @classmethod
    def cnic_format(cls, v):
        if v and not CNIC_RE.match(v.strip()):
            raise ValueError("CNIC must be in format 00000-0000000-0")
        return v

    @field_validator("contact")
    @classmethod
    def contact_format(cls, v):
        if v and not PHONE_RE.match(v.strip()):
            raise ValueError("Contact must be a valid Pakistani number")
        return v

    @field_validator("age")
    @classmethod
    def age_range(cls, v):
        if v is not None and (v < 7 or v > 120):
            raise ValueError("Age must be between 7 and 120")
        return v
    
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