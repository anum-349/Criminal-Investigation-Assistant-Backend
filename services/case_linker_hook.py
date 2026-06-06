
from typing import List, Optional, Tuple
import logging
from datetime import datetime, UTC

from sqlalchemy.orm import Session

from models import Case, CaseLink, CaseLinkSharedEntity
from services import notification_service as notif
from services.case_linker_engine import (
    LinkProposal,
    compute_links_for_case,
    HIGH_CONFIDENCE_THRESHOLD,
)

log = logging.getLogger(__name__)


def recompute_links_for_case(
    db: Session,
    *,
    focal_case_internal_id: int,
    actor_user_id: Optional[int] = None,
) -> List[LinkProposal]:
    """Run engine, reconcile case_links table, fire notifications."""
    log.info("Recomputing links for case_internal_id=%s", focal_case_internal_id)

    try:
        proposals = compute_links_for_case(
            db, focal_case_id=focal_case_internal_id,
        )
    except Exception:
        log.exception("Engine failed for case_internal_id=%s", focal_case_internal_id)
        return []

    log.info("Engine returned %d proposal(s)", len(proposals))
    for p in proposals:
        log.info("  target=%s type=%s confidence=%.2f headline=%s",
                 p.target_case_id, p.link_type, p.confidence, p.headline[:80])

    existing = (
        db.query(CaseLink)
        .filter(CaseLink.source_case_id == focal_case_internal_id)
        .all()
    )
    existing_by_key = {(l.target_case_id, l.link_type): l for l in existing}
    proposed_keys = {(p.target_case_id, p.link_type) for p in proposals}

    newly_created: List[Tuple[CaseLink, LinkProposal]] = []

    for p in proposals:
        key = (p.target_case_id, p.link_type)
        link = existing_by_key.get(key)
        if link is None:
            link = CaseLink(
                source_case_id=p.source_case_id,
                target_case_id=p.target_case_id,
                link_type=p.link_type,
                similarity_score=p.confidence,
                explanation=p.explanation,  # headline + DETAIL_MARKER + detail
                created_at=datetime.now(UTC),
            )
            db.add(link)
            db.flush()
            newly_created.append((link, p))
            log.info("  → CREATED link id=%s", link.id)
        else:
            changed = False
            if abs((link.similarity_score or 0) - p.confidence) > 1e-4:
                link.similarity_score = p.confidence
                changed = True
            if (link.explanation or "") != p.explanation:
                link.explanation = p.explanation
                changed = True
            if changed:
                log.info("  → UPDATED link id=%s", link.id)

        db.query(CaseLinkSharedEntity).filter(
            CaseLinkSharedEntity.link_id == link.id
        ).delete(synchronize_session=False)
        for ent_type, ent_value, person_id in p.shared_entities:
            db.add(CaseLinkSharedEntity(
                link_id=link.id,
                entity_type=ent_type,
                entity_value=ent_value[:255],
                person_id=person_id,
            ))

    for key, link in existing_by_key.items():
        if key not in proposed_keys:
            log.info("  → DELETING stale link id=%s", link.id)
            db.delete(link)

    for link, proposal in newly_created:
        if (link.similarity_score or 0.0) >= HIGH_CONFIDENCE_THRESHOLD:
            _notify_link_created(db, link=link, proposal=proposal, actor_user_id=actor_user_id)

    return proposals


def _notify_link_created(
    db: Session,
    *,
    link: CaseLink,
    proposal: LinkProposal,
    actor_user_id: Optional[int],
) -> None:
    """Notify both investigators using the engine's pre-built headline."""
    source = db.query(Case).filter(Case.id == link.source_case_id).first()
    target = db.query(Case).filter(Case.id == link.target_case_id).first()
    if not source or not target:
        return

    pct = int(round((link.similarity_score or 0.0) * 100))

    src_inv = _investigator_user_id(db, source)
    tgt_inv = _investigator_user_id(db, target)

    recipients = set()
    if src_inv: recipients.add(src_inv)
    if tgt_inv: recipients.add(tgt_inv)
    if not recipients and actor_user_id:
        recipients.add(actor_user_id)

    if not recipients:
        log.warning("No notification recipients for link id=%s", link.id)
        return

    log.info("Pushing CASE_LINKED to %d user(s): %s", len(recipients), recipients)

    for uid in recipients:
        # Headline IS the message — it's already a complete sentence.
        if uid == tgt_inv and uid != src_inv:
            title = f"Your case {target.case_id} linked to {source.case_id}"
        else:
            title = f"Case Linked: {source.case_id} ↔ {target.case_id}"

        message = f"{proposal.headline} (confidence {pct}%)"
        link_url = (
            f"/investigator/case/{target.case_id}?tab=linked"
            if uid == tgt_inv and uid != src_inv
            else f"/investigator/case/{source.case_id}?tab=linked"
        )

        try:
            notif.push(
                db,
                user_id=uid,
                type="CASE_LINKED",
                title=title,
                message=message,
                link_url=link_url,
                related_case_id=(target.id if uid == tgt_inv and uid != src_inv else source.id),
                severity_label="High" if pct >= 85 else "Normal",
            )
        except Exception:
            log.exception("notif.push failed for user_id=%s", uid)


def _investigator_user_id(db: Session, case: Case) -> Optional[int]:
    """Return User.id for a case's assigned investigator."""
    uid = getattr(case, "assigned_investigator_id", None)
    if uid:
        return uid
    inv = getattr(case, "assigned_to", None)
    if inv and inv.user:
        return inv.user.id
    return None