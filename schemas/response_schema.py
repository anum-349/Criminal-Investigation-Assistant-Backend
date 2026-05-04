from typing import Optional
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field, ConfigDict
# ════════════════════════════════════════════════════════════════════════════
# RESPONSE schemas — what the backend returns
# ════════════════════════════════════════════════════════════════════════════

class TokenResponse(BaseModel):
    """Response from /login and /register — includes JWT + minimal user info.
    Frontend immediately calls /me after login to get the full profile;
    this response only carries enough to identify the user and store
    the token.
    """
    access_token: str
    token_type:   str = "bearer"
    id:           int
    username:     str
    badge_number: Optional[str] = None
    role:         str


class MeResponse(BaseModel):
    """GET /api/auth/me — the AuthContext source-of-truth on the frontend."""
    model_config = ConfigDict(from_attributes=True)

    id:           int
    username:     str
    badge_number: Optional[str] = None
    email:        Optional[str] = None
    role:         str
    status:       Optional[str] = None
    picture_url:  Optional[str] = None
    last_login:   Optional[datetime] = None


class UserResponse(BaseModel):
    """Returned by PUT /api/auth/profile."""
    model_config = ConfigDict(from_attributes=True)

    id:           int
    username:     str
    email:        Optional[str] = None
    contact_info: Optional[str] = None
    address:      Optional[str] = None
    picture_url:  Optional[str] = None


class InvestigatorResponse(BaseModel):
    """Returned by PUT /api/auth/investigator/profile."""
    model_config = ConfigDict(from_attributes=True)

    id:             int
    department:     Optional[str] = None
    rank:           Optional[str] = None
    shift:          Optional[str] = None
    specialization: Optional[str] = None


class InvestigatorProfileResponse(BaseModel):
    """Combined response when both user and investigator rows are updated.

    Matches what update_investigator_profile() returns:
        { "user": user, "investigator": inv }
    """
    user:         UserResponse
    investigator: InvestigatorResponse


class MessageResponse(BaseModel):
    """Generic acknowledgement response — used by /logout, /change-password,
    and any other endpoint that just confirms an action."""
    message: str