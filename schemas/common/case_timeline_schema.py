from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict

class TimelineEventRow(BaseModel):
    """One timeline row. Field names match createSystemEvent/createManualEvent
    output verbatim."""
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id:               str
    caseId:           str           = Field(alias="case_id")
    eventSource:      str           = Field(alias="event_source")    # "system" | "manual" | "ai"
    eventType:        str           = Field(alias="event_type")
    title:            str
    description:      Optional[str] = None
    officerName:      Optional[str] = Field(default=None, alias="officer_name")
    severity:         str           = "Normal"
    location:         Optional[str] = None
    outcome:          Optional[str] = None
    attachmentNote:   Optional[str] = Field(default=None, alias="attachment_note")
    followUpRequired: bool          = Field(default=False, alias="follow_up_required")
    followUpDate:     Optional[str] = Field(default=None, alias="follow_up_date")  # YYYY-MM-DD
    date:             str                      # YYYY-MM-DD
    time:             Optional[str] = None     # HH:MM
    createdAt:        Optional[datetime] = Field(default=None, alias="created_at")
    editable:         bool          = True


class TimelineCounts(BaseModel):
    """Drives the small subtitle 'X auto-logged · Y manual' on the tab."""
    all:    int = 0
    system: int = 0   # includes AI-generated
    ai:     int = 0
    manual: int = 0


class CaseTimelineList(BaseModel):
    """GET /api/investigator/cases/{case_id}/timeline response."""
    items:   List[TimelineEventRow]
    counts:  TimelineCounts

class AddTimelineEventRequest(BaseModel):
    """
    Mirrors what AddTimelineDialog sends. It actually wraps the event in
    `{ caseId, events: [event] }` — the router unwraps that into `events[0]`
    which then maps to this shape.

    The dialog uses MANUAL_EVENT_TYPES values (e.g. "Field Visit",
    "Witness Interview"). The backend stores those on TimelineEventType
    rows (label = "Field Visit", code = "FIELD_VISIT" etc.).
    """
    model_config = ConfigDict(extra="ignore")

    eventType:        str
    title:            str
    description:      Optional[str] = None
    date:             Optional[str] = None      # YYYY-MM-DD (defaults to today)
    time:             Optional[str] = None      # HH:MM    (defaults to now)
    location:         Optional[str] = None
    officerName:      Optional[str] = None
    outcome:          Optional[str] = None
    severity:         Optional[str] = "Normal"
    attachmentNote:   Optional[str] = None
    followUpRequired: bool          = False
    followUpDate:     Optional[str] = None      # YYYY-MM-DD

class DeleteTimelineEventResult(BaseModel):
    deleted_id: str