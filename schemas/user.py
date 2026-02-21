from typing import Optional

from pydantic import BaseModel, EmailStr

class UserRegister(BaseModel):
    username: str
    password: str
    role: str = "investigator"
    secret_code: str = None  # Only required for admin registration

class UserLogin(BaseModel):
    badge_number: str
    password: str
    secret_code: str = None  # Only required for admin login

class UserUpdate(BaseModel):
    email: Optional[EmailStr]
    contact_info: Optional[str]
    address: Optional[str]
    picture_url: Optional[str]

class InvestigatorUpdate(UserUpdate):
    department: Optional[str]
    rank: Optional[str]
    shift: Optional[str]
    specialization: Optional[str]