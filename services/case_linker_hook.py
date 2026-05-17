from __future__ import annotations

import logging
from datetime import datetime, UTC
from typing import List, Optional, Set

from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session

from models import (
    Case,
    CaseLink,
    CaseLinkSharedEntity,
    User,
    Investigator,
)
from services import notification_service as notif
from services.case_linker_engine import (
    LinkProposal,
    compute_links_for_case,
    HIGH_CONFIDENCE_THRESHOLD,
    LINK_TYPE_CODES,
)

log = logging.getLogger(__name__)

# Field names (on the Case model) that we consider "major" for re-linking
# purposes. Updating any of these triggers a re-run; everything else doesn't.
# Keep this conservative — re-running is cheap but not free.
MAJOR_FIELDS: Set[str] = {
    "case_status_id",
    "case_type_id",
    "weapon_id",
    "incident_date",
    "fir_number",
}

# Same set, but for what we consider a "major" related-entity change.
# These aren't fields on Case — they're separate tables. The callers
# (suspect/victim/location services) explicitly call enqueue_linking()
# when they mutate these.


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────
def enqueue_linking(
    db: Session,
    *,
    case_internal_id: int,
    actor_user_id: Optional[int] = None,
    reason: str = "register_or_update",
) -> None:
    """Register an after_commit listener that re-links this case.

    Safe to call multiple times in the same transaction — we de-duplicate
    by stashing the set of pending case IDs on the session's `info` dict
    so we only run ONE pass even if multiple services request linking
    during a single commit.
    """
    if case_internal_id is None:
        return

    # Stash on the session so we can dedupe.
    pending: dict = db.info.setdefault("_linking_pending", {})
    if case_internal_id in pending:
        # Already enqueued for this commit cycle — nothing to do.
        return
    pending[case_internal_id] = {
        "actor_user_id": actor_user_id,
        "reason": reason,
    }

    # Register the after_commit listener once per session. Subsequent calls
    # within the same txn just add to the pending dict.
    if not db.info.get("_linking_listener_attached"):
        db.info["_linking_listener_attached"] = True

        def _on_commit(session):
            # We can't reuse `session` here because it's been committed and
            # `info` may have been cleared on some drivers. We grab the
            # pending set, clear the marker, and open a fresh session
            # bound to the same engine to do the actual work. This also
            # keeps the linking work out of the original transaction
            # (already committed) so a linker bug can't roll back the user's
            # registration.
            pending_snapshot = dict(session.info.get("_linking_pending") or {})
            session.info["_linking_pending"] = {}
            session.info["_linking_listener_attached"] = False

            if not pending_snapshot:
                return

            # Open a fresh, independent session for the linking work.
            # Same engine, separate transaction — failures here log but
            # don't propagate.
            try:
                fresh = Session(bind=session.bind, expire_on_commit=False)
                try:
                    for cid, ctx in pending_snapshot.items():
                        try:
                            recompute_links_for_case(
                                fresh,
                                focal_case_internal_id=cid,
                                actor_user_id=ctx.get("actor_user_id"),
                            )
                        except Exception:
                            log.exception(
                                "Linking failed for case_internal_id=%s", cid
                            )
                    fresh.commit()
                finally:
                    fresh.close()
            except Exception:
                log.exception("Linker session setup failed")

        # once=True so SQLAlchemy auto-detaches after the dispatch loop.
        sa_event.listen(db, "after_commit", _on_commit, once=True)


def did_any_major_field_change(case: Case, dirty_attrs: Set[str]) -> bool:
    """Helper for the update path — returns True if any of the changed
    column names is in our MAJOR_FIELDS set."""
    return bool(dirty_attrs & MAJOR_FIELDS)


# ─────────────────────────────────────────────────────────────────────────────
# Core recomputation
# ─────────────────────────────────────────────────────────────────────────────
def recompute_links_for_case(
    db: Session,
    *,
    focal_case_internal_id: int,
    actor_user_id: Optional[int] = None,
) -> List[LinkProposal]:
    """Run the engine and reconcile the `case_links` table.

    Returns the list of accepted proposals (above MIN_PERSIST_SCORE), so
    callers can use it for analytics / response shaping if needed.
    """
    proposals = compute_links_for_case(
        db, focal_case_id=focal_case_internal_id
    )

    # Load every existing outgoing link from this case so we can:
    #   - update the ones still valid
    #   - delete the ones that no longer score
    existing = (
        db.query(CaseLink)
        .filter(CaseLink.source_case_id == focal_case_internal_id)
        .all()
    )
    existing_by_key = {
        (l.target_case_id, l.link_type): l for l in existing
    }
    proposed_keys = {(p.target_case_id, p.link_type) for p in proposals}

    newly_created: List[CaseLink] = []
    updated: List[CaseLink] = []

    # Upsert each proposal.
    for p in proposals:
        key = (p.target_case_id, p.link_type)
        link = existing_by_key.get(key)

        if link is None:
            # New link
            link = CaseLink(
                source_case_id=p.source_case_id,
                target_case_id=p.target_case_id,
                link_type=p.link_type,
                similarity_score=p.confidence,
                explanation=p.explanation,
                created_at=datetime.now(UTC),
            )
            db.add(link)
            db.flush()  # populate link.id for shared-entity inserts below
            newly_created.append(link)
        else:
            # Existing — refresh score + explanation only if changed
            changed = False
            if abs((link.similarity_score or 0) - p.confidence) > 1e-4:
                link.similarity_score = p.confidence
                changed = True
            if (link.explanation or "") != p.explanation:
                link.explanation = p.explanation
                changed = True
            if changed:
                updated.append(link)

        # Refresh shared_entities — replace fully so we don't accumulate
        # stale rows from previous runs.
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

    # Delete links that no longer meet the threshold.
    for key, link in existing_by_key.items():
        if key not in proposed_keys:
            db.delete(link)

    # Fire notifications for newly created HIGH-CONFIDENCE links only.
    # We don't fire on updates — the user already knows about the link;
    # a confidence bump from 0.78 to 0.82 isn't worth a toast.
    for link in newly_created:
        if (link.similarity_score or 0.0) >= HIGH_CONFIDENCE_THRESHOLD:
            _notify_link_created(db, link=link, actor_user_id=actor_user_id)

    return proposals


# ─────────────────────────────────────────────────────────────────────────────
# Notification fan-out
# ─────────────────────────────────────────────────────────────────────────────
def _notify_link_created(
    db: Session,
    *,
    link: CaseLink,
    actor_user_id: Optional[int],
) -> None:
    """Push a CASE_LINKED notification to both investigators.

    Both because:
      • The investigator on the focal case wants to know "your new case
        matches an existing investigation."
      • The investigator on the target case wants to know "a new case
        matches yours — coordinate."
    """
    source = db.query(Case).filter(Case.id == link.source_case_id).first()
    target = db.query(Case).filter(Case.id == link.target_case_id).first()
    if not source or not target:
        return

    pct = int(round((link.similarity_score or 0.0) * 100))
    relation_label = LINK_TYPE_HUMAN.get(link.link_type, link.link_type)

    # Investigator on the source (focal) case
    src_inv_user = _investigator_user_id(db, source)
    if src_inv_user and src_inv_user != actor_user_id:
        # Don't toast the person who just made the change — they're
        # actively in the UI and will see the linked-cases tab update
        # via the SSE-driven refetch. (Toast feels redundant.)
        # We DO notify them if actor_user_id is None (system-initiated).
        notif.push(
            db,
            user_id=src_inv_user,
            type="CASE_LINKED",
            title=f"Case Linked: {source.case_id} ↔ {target.case_id}",
            message=(
                f"AI matched '{target.case_title or target.case_id}' "
                f"with confidence {pct}% on {relation_label}."
            ),
            link_url=f"/investigator/case/{source.case_id}?tab=linked",
            related_case_id=source.id,
            severity_label="High" if pct >= 85 else "Normal",
        )
    elif src_inv_user is None and actor_user_id:
        # No investigator assigned yet — notify the actor (e.g., the
        # registering officer) as a fallback so the link doesn't vanish.
        notif.push(
            db, user_id=actor_user_id, type="CASE_LINKED",
            title=f"Case Linked: {source.case_id} ↔ {target.case_id}",
            message=(
                f"AI matched '{target.case_title or target.case_id}' "
                f"with confidence {pct}% on {relation_label}."
            ),
            link_url=f"/investigator/case/{source.case_id}?tab=linked",
            related_case_id=source.id,
            severity_label="Normal",
        )

    # Investigator on the target (existing) case — different person from
    # actor in nearly every realistic flow.
    tgt_inv_user = _investigator_user_id(db, target)
    if tgt_inv_user and tgt_inv_user != src_inv_user and tgt_inv_user != actor_user_id:
        notif.push(
            db,
            user_id=tgt_inv_user,
            type="CASE_LINKED",
            title=f"Your case {target.case_id} linked to {source.case_id}",
            message=(
                f"New case '{source.case_title or source.case_id}' matches "
                f"with confidence {pct}% on {relation_label}. Consider "
                "coordinating with the assigned investigator."
            ),
            link_url=f"/investigator/case/{target.case_id}?tab=linked",
            related_case_id=target.id,
            severity_label="High" if pct >= 85 else "Normal",
        )


def _investigator_user_id(db: Session, case: Case) -> Optional[int]:
    """Return the User.id for a case's assigned investigator (the FK we
    want to use with `notif.push`). The Case model has both
    `assigned_to_id` (→ Investigator) and `assigned_investigator_id`
    (→ User) — we prefer the User one if available, else resolve via
    the Investigator."""
    # Direct user FK (used by case_detail_service auth check)
    uid = getattr(case, "assigned_investigator_id", None)
    if uid:
        return uid
    # Fallback via Investigator.user
    inv = getattr(case, "assigned_to", None)
    if inv and inv.user:
        return inv.user.id
    return None


# Mapping from machine codes to friendly text in notifications.
# Keep aligned with the engine's LINK_TYPE_CODES and the frontend's pills.
LINK_TYPE_HUMAN = {
    "SAME_SUSPECT":  "shared suspect",
    "SAME_VICTIM":   "shared victim",
    "SAME_WEAPON":   "shared weapon",
    "SAME_LOCATION": "shared location",
    "SAME_MO":       "shared modus operandi",
    "TEMPORAL":      "temporal proximity",
    "OTHER":         "multiple shared signals",
}