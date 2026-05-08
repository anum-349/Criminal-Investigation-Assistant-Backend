"""
schemas/case_timeline_schema.py
─────────────────────────────────────────────────────────────────────────────
Request + response shapes for the case-detail "Timeline" tab
(src/pages/investigator/case/[id]/CaseTimeline.jsx).

Endpoints these power:
  GET    /api/investigator/cases/{case_id}/timeline                  (list)
  POST   /api/investigator/cases/{case_id}/timeline                  (add manual)
  DELETE /api/investigator/cases/{case_id}/timeline/{event_id}       (delete manual)

Design notes
────────────
The Timeline tab has two kinds of rows:
  • SYSTEM / AI events  — auto-logged by other actions (suspect added, evidence
    added, AI lead generated, …). These are NOT created via this endpoint;
    they are written by the "triple-write" helpers in case_detail_service /
    case_evidence_service / case_lead_service / case_suspect_service.
    They are returned by the GET endpoint as eventSource="system" (or "ai"
    for AI-generated rows).
  • MANUAL events       — typed in by the investigator via AddTimelineDialog.
    Created here, deletable here.

The response shape mirrors createSystemEvent / createManualEvent in the JS
constants file, so the existing CaseTimeline.jsx renders DB rows without any
extra adaptation.
"""

from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict


# ─── Single row (used for GET, POST, internal helpers) ──────────────────────

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


# ─── List response ──────────────────────────────────────────────────────────

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


# ─── POST — Add Manual Timeline Event ───────────────────────────────────────

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


# ─── DELETE response ────────────────────────────────────────────────────────

class DeleteTimelineEventResult(BaseModel):
    deleted_id: str