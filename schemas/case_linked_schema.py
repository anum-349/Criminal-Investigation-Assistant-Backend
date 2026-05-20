from typing import List, Optional
from pydantic import BaseModel, ConfigDict

class LinkedCaseRow(BaseModel):
    """One row in the Linked Cases tab.
 
    Two-tier explanation:
      • relation        — short headline (one-line narrative, used in the
                          table cell as-is)
      • relationDetail  — multi-section markdown breakdown (optional,
                          shown in expandable drawer)
    """
    model_config = ConfigDict(from_attributes=True)
 
    id: str
    linkedCaseId: str
    title: str
    investigator: str
    registerDate: str
    status: str
 
    relation: str                               # headline (always populated)
    relationDetail: Optional[str] = None        
 
    linkType: str
    similarityScore: Optional[int] = None       # 0–100
    explanation: Optional[str] = None           # raw, kept for back-compat
 
class LinkedRelationOption(BaseModel):
    value: str            
    label: str            
    variant: str = "default"


class LinkedStatusOption(BaseModel):
    value: str
    label: str
    variant: str = "default"


class CaseLinkedCasesList(BaseModel):
    items:            List[LinkedCaseRow]
    total:            int
    page:             int
    page_size:        int
    relation_options: List[LinkedRelationOption]
    status_options:   List[LinkedStatusOption]