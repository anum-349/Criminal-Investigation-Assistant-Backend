import time
import logging
from sqlalchemy.orm import Session
from models import Case

log = logging.getLogger(__name__)

def get_case_with_retry(
    db: Session,
    case_id: str,
    max_attempts: int = 5,
    delay_s: float = 0.6,
) -> Case:
    """
    Retry fetching a case to handle the window between caseId being returned
    to the frontend and the DB commit being visible to subsequent queries.
    """
    for attempt in range(1, max_attempts + 1):
        case = (
            db.query(Case)
            .filter(Case.case_id == case_id, Case.is_deleted == False)
            .first()
        )
        if case:
            return case
        log.warning(
            "Case %s not found on attempt %d/%d — retrying in %.1fs",
            case_id, attempt, max_attempts, delay_s,
        )
        time.sleep(delay_s)

    from fastapi import HTTPException
    raise HTTPException(
        status_code=404,
        detail=f"Case {case_id} not found after {max_attempts} attempts. "
               "The case may still be committing — retry in a moment."
    )