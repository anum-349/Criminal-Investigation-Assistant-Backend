# schemas/ticket_schema.py
from pydantic import BaseModel, ConfigDict, field_validator
from typing import Optional, List
from datetime import datetime


class CreateTicketRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    priority: str = "normal"
    subject:  str
    message:  str

    @field_validator("priority")
    @classmethod
    def priority_valid(cls, v):
        if v not in ("normal", "urgent", "critical"):
            raise ValueError("priority must be normal, urgent, or critical")
        return v

    @field_validator("subject")
    @classmethod
    def subject_not_empty(cls, v):
        if not v.strip():
            raise ValueError("Subject is required")
        return v.strip()

    @field_validator("message")
    @classmethod
    def message_not_empty(cls, v):
        if not v.strip():
            raise ValueError("Message is required")
        return v.strip()


class UpdateTicketRequest(BaseModel):
    """Admin-only: change status, assign, add internal note."""
    model_config = ConfigDict(extra="ignore")

    status:      Optional[str] = None   # OPEN / IN_PROGRESS / RESOLVED / CLOSED
    assigned_to: Optional[int] = None   # user_id of admin
    admin_notes: Optional[str] = None


class AddReplyRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    body: str

    @field_validator("body")
    @classmethod
    def body_not_empty(cls, v):
        if not v.strip():
            raise ValueError("Reply body is required")
        return v.strip()


class TicketReplyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:         int
    author_id:  Optional[int]
    author_name: Optional[str]
    body:       str
    is_admin:   bool
    created_at: datetime


class TicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:           int
    ticket_id:    str
    sender_id:    int
    sender_name:  Optional[str]
    priority:     str
    subject:      str
    message:      str
    status:       str          # label
    status_code:  str          # code
    assigned_to_id:   Optional[int]
    assigned_to_name: Optional[str]
    admin_notes:  Optional[str]
    resolved_at:  Optional[datetime]
    created_at:   datetime
    updated_at:   datetime
    replies:      List[TicketReplyOut] = []


class TicketListOut(BaseModel):
    items:     List[TicketOut]
    total:     int
    page:      int
    page_size: int