from typing import List, Optional
from pydantic import BaseModel, ConfigDict

class EvidencePhotoOut(BaseModel):
    """One photo attached to an evidence row."""
    model_config = ConfigDict(from_attributes=True)

    id:        int                
    url:       str                
    file_name: Optional[str] = None
    caption:   Optional[str] = None

class CaseEvidenceRow(BaseModel):
    """One row in the Evidences-tab table. Field names match the JSX (id,
    type, date, description, collectedBy, status, photos)."""
    model_config = ConfigDict(from_attributes=True)

    id:           str                      # external evidence_id, e.g. "EVID-001"
    type:         str
    description:  Optional[str] = None
    date:         str                      # date_collected, YYYY-MM-DD
    collectedBy:  Optional[str] = None
    status:       str                      # "Analyzed" | "Pending Analysis"
    photos:       List[EvidencePhotoOut] = []
    fileName:     Optional[str] = None
    fileMime:     Optional[str] = None

class CaseEvidenceList(BaseModel):
    """Server-side filtered + paginated list returned to the page."""
    items:           List[CaseEvidenceRow]
    total:           int
    page:            int
    page_size:       int
    type_options:    List[str]             # for the type column / future dropdown
    status_options:  List[str]             # always ["Analyzed", "Pending Analysis"]

class UpdateEvidenceRequest(BaseModel):
    """PATCH body — only fields the user can edit from the Update dialog."""
    type:           Optional[str] = None
    description:    Optional[str] = None
    dateCollected:  Optional[str] = None   # YYYY-MM-DD
    collectedBy:    Optional[str] = None
    status:         Optional[str] = None   # "Analyzed" | "Pending Analysis"
    
    fileDataUrl:   Optional[str] = None   # base64 data URL, any MIME
    fileName:      Optional[str] = None   # original filename, used for extension
    fileMime:      Optional[str] = None   # MIME hint from the browser

class PhotoUploadRequest(BaseModel):
    """Photo upload as a base-64 data URL (matches what FileReader produces
    in the existing CaseEvidence dialog). The service strips the prefix and
    decodes the body itself."""
    dataUrl:    str
    fileName:   Optional[str] = None
    caption:    Optional[str] = None

class PhotoUploadResult(BaseModel):
    photo:   EvidencePhotoOut
    photos:  List[EvidencePhotoOut]        # full updated list, for convenience

class PhotoDeleteResult(BaseModel):
    deleted_id: int
    photos:     List[EvidencePhotoOut]