from typing import Optional
from pydantic import BaseModel, EmailStr, Field, ConfigDict


class UserRegister(BaseModel):
    username:    str = Field(min_length=3, max_length=80)
    password:    str = Field(min_length=8, max_length=128)
    role:        str = "investigator"
    email:       Optional[EmailStr] = None
    secret_code: Optional[str] = None   # required only for admin role


class UserLogin(BaseModel):
    identifier:  str = Field(min_length=3, description="Username or badge number")
    password:    str = Field(min_length=1)
    secret_code: Optional[str] = None   # required only for admin role


class UserUpdate(BaseModel):
    """Pydantic v2 — Optional[X] alone is required, so explicit `= None`."""
    model_config = ConfigDict(extra="ignore")

    email:        Optional[EmailStr] = None
    contact_info: Optional[str]      = None
    address:      Optional[str]      = None
    picture_url:  Optional[str]      = None


class InvestigatorUpdate(UserUpdate):
    department:     Optional[str] = None
    rank:           Optional[str] = None
    shift:          Optional[str] = None
    specialization: Optional[str] = None


class TokenResponse(BaseModel):
    """What login/register return. Frontend stores access_token and uses
    it for the Authorization: Bearer header on every request."""
    access_token: str
    token_type:   str = "bearer"
    id:           int
    username:     str
    badge_number: Optional[str] = None
    role:         str