from typing import Optional
from datetime import date as _date
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

class CaseLocationInput(BaseModel):
    """Step 3 — Crime Scene Location."""
    model_config = ConfigDict(extra="ignore")

    province:       Optional[str]   = None
    city:           Optional[str]   = None
    area:           Optional[str]   = None
    policeStation:  Optional[str]   = None
    address:        Optional[str]   = None
    latitude:       Optional[float] = None
    longitude:      Optional[float] = None
    crimeSceneType: Optional[str]   = None
    sceneAccess:    Optional[str]   = None
    landmarks:      Optional[str]   = None


class CrimeDetailsInput(BaseModel):
    """Step 6 — Crime Details.

    Includes the common columns plus the crime-type-specific extras
    (Murder / SexualAssault / Theft). The service decides which subtype
    table to write based on the case_type chosen in the parent payload.
    Unused extras are ignored, not rejected — same forgiving shape the
    wizard uses.
    """
    model_config = ConfigDict(extra="ignore")

    # Common
    weaponUsed:        Optional[str]  = None
    weaponDescription: Optional[str]  = None
    vehicleUsed:       Optional[str]  = None
    numSuspects:       Optional[int]  = None
    motive:            Optional[str]  = None
    modus:             Optional[str]  = None
    witnessAvailable:  Optional[bool] = False
    cctv:              Optional[bool] = False
    crimeDescription:  Optional[str]  = None

    # Murder extras
    causeOfDeath:    Optional[str]  = None
    bodyLocation:    Optional[str]  = None
    timeOfDeath:     Optional[str]  = None
    postmortemDone:  Optional[bool] = False
    forensicDone:    Optional[bool] = False

    # Sexual-assault extras
    medicalExam:      Optional[str]  = None
    victimCounseling: Optional[bool] = False
    protectionOrder:  Optional[bool] = False

    # Theft extras
    stolenItems:    Optional[str]   = None
    stolenValue:    Optional[float] = None
    recoveryStatus: Optional[str]   = None
    entryPoint:     Optional[str]   = None


class CaseRegisterRequest(BaseModel):
    """Body for POST /api/investigator/cases.

    Required fields (enforced below):
        firNumber, caseTitle, caseType, priority, description,
        incidentDate, reportingDate, and inside location → province, city,
        address; inside crime → crimeDescription.

    The required set mirrors validateStep() in
    src/pages/form/case-forms/register-case/page.jsx, so a fresh wizard
    submission will pass validation here too.
    """
    model_config = ConfigDict(extra="ignore")

    # ── Case meta ──────────────────────────────────────────────────────
    firNumber:   Optional[str] = None
    caseTitle:   Optional[str] = None
    caseType:    Optional[str] = None     # CaseType.label
    priority:    Optional[str] = None     # Severity.label
    caseStatus:  Optional[str] = "Open"   # CaseStatus.label
    ppcSections: Optional[str] = None
    description: Optional[str] = None

    incidentDate:  Optional[str] = None    # YYYY-MM-DD
    incidentTime:  Optional[str] = None
    reportingDate: Optional[str] = None    # YYYY-MM-DD
    reportingTime: Optional[str] = None

    reportingOfficer:     Optional[str] = None
    assignedInvestigator: Optional[str] = None   # username; admin-only

    # FIR metadata. The file itself is uploaded via a follow-up PATCH
    # /cases/{caseId}/fir-file — keeps this body small.
    firLanguage: Optional[str]  = "English"
    firType:     Optional[str]  = "FIR"
    manualEntry: Optional[bool] = False

    # ── Nested ─────────────────────────────────────────────────────────
    location: CaseLocationInput = Field(default_factory=CaseLocationInput)
    crime:    CrimeDetailsInput = Field(default_factory=CrimeDetailsInput)

    # ── Validators ─────────────────────────────────────────────────────
    @field_validator("incidentDate", "reportingDate")
    @classmethod
    def _check_date_format(cls, v: Optional[str]):
        """Accept YYYY-MM-DD only; let strptime do the heavy lifting."""
        if v is None or v == "":
            return v
        try:
            _date.fromisoformat(v)
        except ValueError:
            raise ValueError("Date must be in YYYY-MM-DD format")
        return v

    @model_validator(mode="after")
    def _require_core_fields(self):
        """Same required set as the client-side validateStep().

        We raise a 422 with a list of missing fields instead of a single
        terse error so the frontend can highlight every red field on one
        round-trip rather than playing whack-a-mole.
        """
        missing = []

        def need(field_name: str, value):
            if value is None or (isinstance(value, str) and not value.strip()):
                missing.append(field_name)

        # Step 2 — Case Details
        need("firNumber",     self.firNumber)
        need("caseTitle",     self.caseTitle)
        need("caseType",      self.caseType)
        need("priority",      self.priority)
        need("description",   self.description)
        need("incidentDate",  self.incidentDate)
        need("reportingDate", self.reportingDate)

        # Step 3 — Location
        if self.location:
            need("location.province", self.location.province)
            need("location.city",     self.location.city)
            need("location.address",  self.location.address)

        # Step 6 — Crime Details
        if self.crime:
            need("crime.crimeDescription", self.crime.crimeDescription)

        if missing:
            raise ValueError(
                "Missing required fields: " + ", ".join(missing)
            )

        return self


# ──────────────────────────────────────────────────────────────────────────
# Response body
# ──────────────────────────────────────────────────────────────────────────

class CaseRegisterResponse(BaseModel):
    """Returned by POST /api/investigator/cases."""
    model_config = ConfigDict(from_attributes=True)

    case_id:    str       # external case_id, e.g. "C-2053"
    fir_number: str
    # The system "CASE_REGISTERED" timeline event(s) the service writes
    # at create-time. The frontend doesn't need to use these immediately,
    # but they're handy if you want to render the timeline right after
    # the wizard completes without an extra GET round-trip.
    timeline_events: list = []


# ──────────────────────────────────────────────────────────────────────────
# Follow-up FIR file upload
# ──────────────────────────────────────────────────────────────────────────

class FIRFileUploadRequest(BaseModel):
    """Body for PATCH /api/investigator/cases/{caseId}/fir-file.

    Matches the data-URL convention used by evidence + person photo
    uploads, so the backend can reuse _decode_data_url() unchanged.
    """
    model_config = ConfigDict(extra="ignore")

    fileDataUrl: str
    fileName:    Optional[str] = None
    fileMime:    Optional[str] = None


class FIRFileUploadResult(BaseModel):
    fir_file_url:  str
    fir_file_name: Optional[str] = None