from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from db import get_db
from dependencies.auth import get_current_user
from models import User
from schemas.case_detail_schema import DeleteDraftResponse, DraftDetailResponse, DraftListResponse, SaveDraftRequest
from services.case_detail_service import delete_draft, get_draft, list_drafts, save_draft

router = APIRouter()

@router.post(
    "",
    response_model=DraftDetailResponse,
    summary="Create a new draft, or update an existing one (pass draftId).",
)
def save_draft_endpoint(
    body: SaveDraftRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DraftDetailResponse:
    """Save the wizard's current state.
 
    The frontend calls this from RegisterCasePage in two places:
      - Manual "Save Draft" button.
      - The 30-second autosave loop (when prefs.auto_save_drafts is on).
    Both pass the same payload shape (SaveDraftRequest); the service
    decides whether to insert or update based on `draftId`.
    """
    return save_draft(db, user=user, body=body)
 
@router.get(
    "",
    response_model=DraftListResponse,
    summary="List the current user's drafts (newest first).",
)
def list_drafts_endpoint(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DraftListResponse:
    """Backs the /investigator/drafts page in the UI."""
    return list_drafts(db, user=user)
 
@router.get(
    "/{draft_id}",
    response_model=DraftDetailResponse,
    summary="Load one draft's full formData (used to resume the wizard).",
)
def get_draft_endpoint(
    draft_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DraftDetailResponse:
    return get_draft(db, user=user, draft_id=draft_id)
 
 
@router.delete(
    "/{draft_id}",
    response_model=DeleteDraftResponse,
    summary="Delete a draft. Also called automatically after a successful registration.",
)
def delete_draft_endpoint(
    draft_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DeleteDraftResponse:
    return delete_draft(db, user=user, draft_id=draft_id)
 