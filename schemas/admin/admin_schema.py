from datetime import datetime
from typing import List, Optional, Dict
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class AdminStatTile(BaseModel):
    """One tile in the dashboard's top KPI strip."""
    label: str
    value: str
    change: Optional[str] = None
    trend:  Optional[str] = None   # "up" | "down" | "flat"
    hint:   Optional[str] = None


class AdminMonthlyTrendItem(BaseModel):
    month:  str
    cases:  int
    solved: int
    users:  int


class AdminCaseStatusItem(BaseModel):
    name:  str
    value: int


class AdminUsersByRole(BaseModel):
    role:  str
    count: int


class AdminRecentLogItem(BaseModel):
    id:     str
    user:   str
    action: str
    module: str
    time:   str          # relative ("10 min ago")
    status: str          # "Success" | "Failed"


class AdminAlertItem(BaseModel):
    id:       str
    title:    str
    detail:   str
    severity: str        # "critical" | "warning" | "info"
    time:     str


class AdminDashboardResponse(BaseModel):
    """Single payload for the admin home page."""
    stats:           List[AdminStatTile]
    monthly_trend:   List[AdminMonthlyTrendItem]
    case_status:     List[AdminCaseStatusItem]
    users_by_role:   List[AdminUsersByRole]
    recent_logs:     List[AdminRecentLogItem]
    alerts:          List[AdminAlertItem]

class AdminUserRow(BaseModel):
    """One row in the user management table."""
    model_config = ConfigDict(from_attributes=True)

    id:           str                # external "USR-001" or numeric pk as string
    full_name:    str
    username:     str
    email:        str
    role:         str                # "Admin" | "Investigator"
    phone:        Optional[str] = None
    department:   Optional[str] = None
    status:       str                # "Active" | "Inactive"
    last_login:   Optional[str] = None   # "YYYY-MM-DD HH:MM" or "—"
    created:      Optional[str] = None   # "YYYY-MM-DD"


class AdminUserListResponse(BaseModel):
    items:     List[AdminUserRow]
    total:     int
    page:      int
    page_size: int


class AdminCreateUserRequest(BaseModel):
    """Body for POST /api/admin/users. Lays on top of UserRegister but
    adds fields admins explicitly set when provisioning an account."""
    model_config = ConfigDict(extra="ignore")

    full_name:  str         = Field(min_length=2,  max_length=120)
    username:   str         = Field(min_length=3,  max_length=80)
    email:      EmailStr
    password:   str         = Field(min_length=8,  max_length=128)
    role:       str         = Field(description="'Admin' or 'Investigator'")
    phone:      Optional[str] = None
    department: Optional[str] = None
    status:     str         = "Active"
    rank:       Optional[str] = None

    @field_validator("role")
    @classmethod
    def role_valid(cls, v):
        if v not in ("Admin", "Investigator"):
            raise ValueError("role must be 'Admin' or 'Investigator'")
        return v


class AdminUpdateUserRequest(BaseModel):
    """PATCH /api/admin/users/{id}. All fields optional."""
    model_config = ConfigDict(extra="ignore")

    full_name:  Optional[str] = None
    email:      Optional[EmailStr] = None
    phone:      Optional[str] = None
    department: Optional[str] = None
    rank:       Optional[str] = None
    status:     Optional[str] = None    # "Active" | "Inactive"
    password:   Optional[str] = Field(default=None, min_length=8, max_length=128)


class AdminToggleStatusResponse(BaseModel):
    id:     str
    status: str


class AdminDeleteUserResponse(BaseModel):
    deleted: bool
    id:      str

class AssignCaseRequest(BaseModel):
    """PATCH /api/admin/cases/{caseId}/assign — re-assign to another investigator."""
    model_config = ConfigDict(extra="ignore")

    investigator_username: str = Field(description="Username of the new investigator")
    reason: Optional[str] = None


class AssignCaseResponse(BaseModel):
    case_id:       str
    investigator:  str
    assigned_at:   datetime


class DeleteCaseResponse(BaseModel):
    deleted:  bool
    case_id:  str

class AuditLogStats(BaseModel):
    total_events: int
    today: int
    failed: int
    unique_users: int

class AuditLogItem(BaseModel):
    id: str
    user: str
    action: str
    module: str
    detail: str
    date: str        # "YYYY-MM-DD"
    time: str        # "HH:MM"
    ip: str
    machine: str
    status: str      # "Success" | "Failed"

class AuditLogsResponse(BaseModel):
    items: list[AuditLogItem]
    total: int
    page: int
    page_size: int
    stats: AuditLogStats


class DatabaseStatsResponse(BaseModel):
    size:            str
    size_note:       Optional[str] = None
    tables:          int
    records:         int
    last_optimized:  Optional[str] = None
    integrity:       str
    connections:     Optional[int] = None
 
 
class BackupRecordOut(BaseModel):
    id:           int
    backup_id:    str
    type:         str
    status:       str
    file_size:    Optional[int] = None
    started_at:   Optional[datetime] = None
    completed_at: Optional[datetime] = None
    initiated_by: Optional[str] = None   # username
 
    class Config:
        from_attributes = True
 
 
class BackupListResponse(BaseModel):
    items: List[BackupRecordOut]
 
 
class SimpleOkResponse(BaseModel):
    ok:      bool = True
    message: str  = ""