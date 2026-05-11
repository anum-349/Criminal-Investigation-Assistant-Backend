from typing import Dict, Optional
from pydantic import BaseModel, EmailStr, Field, ConfigDict

class UserRegister(BaseModel):
    """POST /api/auth/register"""
    username:    str = Field(min_length=3, max_length=80)
    password:    str = Field(min_length=8, max_length=128)
    role:        str = "investigator"
    email:       Optional[EmailStr] = None
    secret_code: Optional[str] = None   # required only for admin role


class UserLogin(BaseModel):
    """POST /api/auth/login

    `identifier` accepts EITHER a username OR a badge_number — backend
    matches both columns. This is what gives the frontend flexibility.
    """
    identifier:  str = Field(min_length=3, description="Username or badge number")
    password:    str = Field(min_length=1)
    secret_code: Optional[str] = None   # required only for admin role


class UserUpdate(BaseModel):
    """PUT /api/auth/profile — common user fields editable by anyone."""
    model_config = ConfigDict(extra="ignore")

    email:        Optional[EmailStr] = None
    contact_info: Optional[str]      = None
    address:      Optional[str]      = None
    picture_url:  Optional[str]      = None


class InvestigatorUpdate(UserUpdate):
    """PUT /api/auth/investigator/profile — extends UserUpdate."""
    department:     Optional[str] = None
    rank:           Optional[str] = None
    shift:          Optional[str] = None
    specialization: Optional[str] = None


class PictureUploadRequest(BaseModel):
    data_url: str   # base64 data URL from the frontend

class PasswordChange(BaseModel):
    """POST /api/auth/change-password

    The backend's _validate_password() is the source of truth on
    complexity rules. Frontend can mirror length-min for instant feedback,
    but the backend must always re-check.
    """
    current_password: str = Field(min_length=1, description="Current password (for verification)")
    new_password:     str = Field(min_length=8, max_length=128, description="New password (min 8 chars, must contain upper/lower/digit)")

class PreferencesPayload(BaseModel):
    preferences: Dict[str, bool]  # {"email_notifications": true, ...}

class PreferencesResponse(BaseModel):
    preferences: Dict[str, bool]

class PersonPhotoUploadRequest(BaseModel):
    """Photo upload as a base-64 data URL (matches what FileReader produces)."""
    dataUrl:  str
    fileName: Optional[str] = None
    caption:  Optional[str] = None

class PersonPhotoUploadResult(BaseModel):
    photoUrl: str  

class PersonPhotoDeleteResult(BaseModel):
    deleted: bool
    photoUrl: Optional[str] = None
