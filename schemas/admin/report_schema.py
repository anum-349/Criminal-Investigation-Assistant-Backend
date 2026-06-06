from pydantic import BaseModel, ConfigDict, field_validator
from typing import Optional, List, Any
from datetime import datetime


class ReportFilters(BaseModel):
    model_config = ConfigDict(extra="ignore")

    caseId:        Optional[str]   = None
    dateFrom:      Optional[str]   = None   # YYYY-MM-DD
    dateTo:        Optional[str]   = None
    province:      Optional[str]   = None
    crimeType:     Optional[str]   = None
    minConfidence: Optional[float] = 60.0
    format:        str             = "PDF"  # PDF | CSV

    @field_validator("format")
    @classmethod
    def format_valid(cls, v):
        if v.upper() not in ("PDF", "CSV"):
            raise ValueError("format must be PDF or CSV")
        return v.upper()

    @field_validator("minConfidence")
    @classmethod
    def confidence_range(cls, v):
        if v is not None and not (0 <= v <= 100):
            raise ValueError("minConfidence must be 0–100")
        return v


class ReportSection(BaseModel):
    heading: str
    rows:    List[List[str]]


class ReportData(BaseModel):
    title:    str
    sections: List[ReportSection]


class GenerateReportRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    reportType: str
    filters:    ReportFilters

    @field_validator("reportType")
    @classmethod
    def type_valid(cls, v):
        valid = {"case_summary", "crime_hotspot", "case_timeline",
                 "leads_report", "suspect_report"}
        if v not in valid:
            raise ValueError(f"reportType must be one of {valid}")
        return v


class ReportHistoryItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:          int
    report_id:   str
    report_type: str
    filters:     Any
    format:      str
    generated_at: datetime
    file_size:   Optional[int]


class ReportHistoryList(BaseModel):
    items: List[ReportHistoryItem]
    total: int