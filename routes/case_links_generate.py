from __future__ import annotations

import logging
from datetime import datetime, UTC
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from db import get_db
from models import Case, CaseLink, User
from dependencies.auth import get_current_user   

from services.case_linker_hook import recompute_links_for_case
from services import semantic_similarity as semsim

log = logging.getLogger(__name__)

router = APIRouter()

class RegenerateLinksResponse(BaseModel):
    """What the frontend uses to drive the toast message."""
    caseId: str                
    linksFound: int             # total accepted proposals (above MIN_PERSIST_SCORE)
    newLinks: int               # rows that didn't exist before this run
    updatedLinks: int           # rows that were already present (score may have changed)
    semanticAvailable: bool     # warns the frontend if SBERT wasn't loaded
    completedAt: str            # ISO timestamp for the toast


def _ensure_can_regenerate(case: Case, user: User) -> None:
    """Investigator assigned to the case OR admin role. Raises 403 if
    neither. Adjust the role-name string to match your User model."""
    role = (getattr(user, "role", "") or "").lower()
    if role in ("admin", "administrator", "super_admin"):
        return
    if case.assigned_investigator_id == user.id:
        return
    
    inv = getattr(case, "assigned_to", None)
    if inv and inv.user and inv.user.id == user.id:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Only the assigned investigator or an admin can regenerate links for this case.",
    )


@router.post(
    "/{case_id}/links/regenerate",
    response_model=RegenerateLinksResponse,
)
def regenerate_links(
    case_id: str = Path(..., description="External case ID, e.g. C-2053"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RegenerateLinksResponse:
    """Re-run the linking engine for one case, return counts."""

    case = (
        db.query(Case)
        .filter(Case.case_id == case_id, Case.is_deleted == False)  # noqa: E712
        .first()
    )
    if not case:
        raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

    _ensure_can_regenerate(case, user)

    semantic_available = semsim.is_available()
    if not semantic_available:
        log.warning(
            "Regenerate for %s running WITHOUT SBERT — "
            "narrative_semantic dimension will score 0.",
            case_id,
        )

    pre_existing_keys = {
        (l.target_case_id, l.link_type)
        for l in db.query(CaseLink)
                    .filter(CaseLink.source_case_id == case.id)
                    .all()
    }

    try:
        proposals = recompute_links_for_case(
            db,
            focal_case_internal_id=case.id,
            actor_user_id=user.id,
        )
    except Exception:
        log.exception("Regenerate failed for case_id=%s", case_id)
        raise HTTPException(
            status_code=500,
            detail="Link regeneration failed. Check server logs.",
        )

    new_links = sum(1 for p in proposals if (p.target_case_id, p.link_type) not in pre_existing_keys)
    updated_links = len(proposals) - new_links

    db.commit()

    log.info(
        "Regenerate complete for %s: %d total (%d new, %d updated) by user=%s",
        case_id, len(proposals), new_links, updated_links, user.id,
    )

    return RegenerateLinksResponse(
        caseId=case_id,
        linksFound=len(proposals),
        newLinks=new_links,
        updatedLinks=updated_links,
        semanticAvailable=semantic_available,
        completedAt=datetime.now(UTC).isoformat(),
    )