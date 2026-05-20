"""
scripts/run_linker_backfill.py  —  v4-compatible
─────────────────────────────────────────────────────────────────────────────
DIAGNOSE + BACKFILL for the case-linking engine, updated for the v3 two-tier
explanations and v4 SBERT semantic dimension.

WHAT'S NEW vs the earlier version
─────────────────────────────────
  • --embeddings   Pre-compute SBERT vectors for every case BEFORE linking.
                   Massively speeds up --backfill on multi-case DBs because
                   each pair's semantic score becomes a cached lookup
                   instead of a model.encode() round-trip.

  • --full         Convenience shorthand for: --embeddings --backfill.
                   This is the command you want for "set up everything
                   from scratch."

  • diagnose mode  Rewritten against the v3 EdgeScore API:
                       edge.total          (unchanged)
                       edge.primary        (was edge.primary_feature)
                       edge.dimensions     (was edge.feature_scores + .reasons)
                   Each fired dimension is shown with its score, weighted
                   contribution, AND its headline phrase.

  • _score_pair    v4 signature: takes (db, features_a, features_b, case_a,
                   case_b). The diagnose pass now passes all five — the
                   previous call (2 args) would crash on the new engine.

USAGE
─────
  # The typical first-time setup — embeddings then links:
  python -m scripts.run_linker_backfill --full

  # Just relink (skip embedding pass — assumes vectors are current):
  python -m scripts.run_linker_backfill --backfill

  # Just pre-compute embeddings (no linker run):
  python -m scripts.run_linker_backfill --embeddings

  # See what the engine thinks about each case + which pairs would link:
  python -m scripts.run_linker_backfill --diagnose

  # Deep-dive one case:
  python -m scripts.run_linker_backfill --diagnose --case C-2053

EXIT CODES
──────────
  0   success
  1   bad args (no flag supplied)
  2   embedder failed to load (only matters with --embeddings)
"""
from __future__ import annotations

import argparse
import logging
import sys
from typing import List, Optional

# ─── Project imports — adjust paths if your layout differs ─────────────────
from db import SessionLocal
from models import Case, CaseLink
from sqlalchemy.orm import joinedload
from models import CaseSuspect, CaseVictim, Evidence

from services.case_linker_engine import (
    _build_features,
    _score_pair,
    _make_proposal,
    MIN_PERSIST_SCORE,
    HIGH_CONFIDENCE_THRESHOLD,
    WEIGHTS,
)
from services.case_linker_hook import recompute_links_for_case
from services import semantic_similarity as semsim
from fir_api.pipeline.model_loader import models as ml_models, warmup
# from fir_api.pipeline.model_loader import warmup


# ─── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
)
log = logging.getLogger("linker-backfill")


# ════════════════════════════════════════════════════════════════════════════
# Helpers used by both modes
# ════════════════════════════════════════════════════════════════════════════
# Standard eager-load bundle. The engine itself loads these too, but doing
# it here makes the script equally fast regardless of which mode is running.
_CASE_LOADS = [
    joinedload(Case.suspects).joinedload(CaseSuspect.person),
    joinedload(Case.victims).joinedload(CaseVictim.person),
    joinedload(Case.location),
    joinedload(Case.weapon),
    joinedload(Case.murder_details),
    joinedload(Case.case_type),
    joinedload(Case.evidences).joinedload(Evidence.type),
]


def _load_one_case(db, internal_id: int) -> Optional[Case]:
    return (
        db.query(Case)
        .options(*_CASE_LOADS)
        .filter(Case.id == internal_id, Case.is_deleted == False)  # noqa: E712
        .first()
    )


def _load_one_case_by_external(db, external_id: str) -> Optional[Case]:
    return (
        db.query(Case)
        .options(*_CASE_LOADS)
        .filter(Case.case_id == external_id, Case.is_deleted == False)  # noqa: E712
        .first()
    )


def _list_all_cases(db) -> List[Case]:
    return (
        db.query(Case)
        .options(*_CASE_LOADS)
        .filter(Case.is_deleted == False)  # noqa: E712
        .order_by(Case.id.asc())
        .all()
    )


def _ensure_embedder_loaded() -> bool:
    """Make sure the SBERT embedder is loaded. Returns True if ready,
    False if it failed (caller should bail when --embeddings was asked)."""
    if ml_models.sentence_embedder is not None:
        return True
    log.info("SBERT embedder not loaded yet — initialising now…")
    try:
        warmup()
    except Exception:
        log.exception("Embedder load failed")
        return False
    return ml_models.sentence_embedder is not None


# ════════════════════════════════════════════════════════════════════════════
# Mode: --embeddings  (pre-compute SBERT vectors)
# ════════════════════════════════════════════════════════════════════════════
def run_embeddings_backfill(*, batch_size: int = 32, limit: Optional[int] = None) -> int:
    """Pre-compute the multilingual MiniLM vector for every case without
    a current one. Idempotent — only computes missing or stale rows.
    Returns the count computed."""
    if not _ensure_embedder_loaded():
        log.error("Cannot run --embeddings: embedder failed to load. "
                  "Check the fir_api.pipeline.model_loader logs.")
        return -1

    db = SessionLocal()
    try:
        count = semsim.backfill_embeddings(
            db, batch_size=batch_size, limit=limit,
        )
        log.info("Embedding pass complete: %d vector(s) written", count)
        return count
    finally:
        db.close()


# ════════════════════════════════════════════════════════════════════════════
# Mode: --backfill  (run linker on every case, write to case_links)
# ════════════════════════════════════════════════════════════════════════════
def run_links_backfill() -> None:
    """Run the linker on every case. Writes/updates case_links and
    case_link_shared_entities. Idempotent."""
    db = SessionLocal()
    try:
        cases = _list_all_cases(db)
        log.info("Backfilling links across %d case(s)", len(cases))

        before = db.query(CaseLink).count()
        log.info("Existing case_links rows: %d", before)

        for i, case in enumerate(cases, start=1):
            try:
                proposals = recompute_links_for_case(
                    db,
                    focal_case_internal_id=case.id,
                    actor_user_id=None,   # system-initiated; no actor toast
                )
                if proposals:
                    log.info(
                        "[%d/%d] %s → %d link(s): %s",
                        i, len(cases), case.case_id, len(proposals),
                        ", ".join(
                            f"→{p.target_case_id}({p.link_type} {p.confidence:.2f})"
                            for p in proposals
                        ),
                    )
                else:
                    log.debug("[%d/%d] %s → no links", i, len(cases), case.case_id)
            except Exception:
                log.exception("Linker failed for %s", case.case_id)

        db.commit()
        after = db.query(CaseLink).count()
        log.info(
            "Link backfill complete: case_links %d → %d (delta=%+d)",
            before, after, after - before,
        )
    finally:
        db.close()


# ════════════════════════════════════════════════════════════════════════════
# Mode: --diagnose
# ════════════════════════════════════════════════════════════════════════════
def _print_case_fingerprint(db, case: Case) -> None:
    """Print everything the linker sees for ONE case. If a case isn't
    matching others when you expect it to, this is where you find out
    why — the fingerprint surfaces exactly which suspect_keys, weapon
    label, location, etc. the engine reads."""
    f = _build_features(db, case)
    print(f"\n┌─ {case.case_id}  ({case.case_title or '—'})")
    print(f"│  internal_id    : {case.id}")
    print(f"│  incident_date  : {case.incident_date}")
    print(f"│  incident_hour  : {f.incident_hour}")
    print(f"│  crime_type     : {f.crime_type_label} (family={f.crime_type_family})")
    print(f"│  city           : {f.city_label} (id={f.city_id})")
    print(f"│  police_station : {f.police_station}")
    print(f"│  area           : {f.area}")
    print(f"│  weapon         : {f.weapon_label} (family={f.weapon_family})")
    print(f"│  motive         : {f.motive}")
    print(f"│  modus_operandi : {f.modus_operandi}")
    print(f"│  cause_of_death : {f.cause_of_death}")
    print(f"│  suspects ({len(f.suspects)}):")
    for k, info in sorted(f.suspects.items()):
        ident = info.cnic or info.full_name or "??"
        print(f"│      {k}  →  {ident} (pid={info.person_id})")
    if not f.suspects:
        print(f"│      (none — linker will not match this case on suspect)")
    print(f"│  victims ({len(f.victims)}):")
    for k, info in sorted(f.victims.items()):
        ident = info.cnic or info.full_name or "??"
        print(f"│      {k}  →  {ident} (pid={info.person_id})")
    print(f"│  evidence_types : {sorted(f.evidence_types) or '(none)'}")
    print(f"└─")


def _print_pair_scores(db, focal_case: Case) -> None:
    """Score the focal case against every other live case and print the
    breakdown. Uses the v4 _score_pair signature (db + ORM objects)."""
    other_cases = (
        db.query(Case)
        .options(*_CASE_LOADS)
        .filter(Case.is_deleted == False, Case.id != focal_case.id)  # noqa: E712
        .all()
    )
    focal_f = _build_features(db, focal_case)
    print(f"\nScoring {focal_case.case_id} against {len(other_cases)} other case(s):")
    print(f"  persist threshold: {MIN_PERSIST_SCORE} | "
          f"notify threshold: {HIGH_CONFIDENCE_THRESHOLD}")
    print("-" * 80)

    # Pre-warm the focal embedding once so the diagnose pass is fast.
    if ml_models.sentence_embedder is not None:
        semsim.get_embedding(db, focal_case)

    any_above = False
    for other in other_cases:
        of = _build_features(db, other)
        # v4 signature: pass session + both ORM objects
        edge = _score_pair(db, focal_f, of, focal_case, other)
        if edge.total < 0.01:
            continue   # don't print near-zero pairs (noise)

        flag = "🟢 LINK " if edge.total >= MIN_PERSIST_SCORE else "   weak "
        print(f"  {flag} {other.case_id:>12s}  score={edge.total:.2f}  "
              f"primary={edge.primary}")

        # Show each fired dimension's contribution. This is the "audit
        # trail" the SHAP-style visualisation surfaces in the UI.
        for name, dim in edge.dimensions.items():
            weight = WEIGHTS.get(name, 0.0)
            contribution = dim.score * weight
            phrase = dim.headline_phrase or "—"
            print(f"          [{name:18s}] score={dim.score:.2f}  "
                  f"weight={weight:.2f}  contrib={contribution:.3f}")
            print(f"               → {phrase}")
        if edge.total >= MIN_PERSIST_SCORE:
            any_above = True

    if not any_above:
        print("\n  (no pairs scored above the persist threshold)")
        print("  Common causes:")
        print("    • Same person added twice with different CNICs / no CNIC")
        print("    • Cases too far apart in time AND space, no shared entity")
        print("    • Embedder not loaded → narrative_semantic always 0")


def run_diagnose(case_external_id: Optional[str] = None) -> None:
    """Diagnostic mode. With no --case, prints every case's fingerprint
    plus a global list of pairs above threshold. With --case X, deep-
    dives that case."""
    db = SessionLocal()
    try:
        if case_external_id:
            case = _load_one_case_by_external(db, case_external_id)
            if not case:
                log.error("Case %s not found", case_external_id)
                return
            _print_case_fingerprint(db, case)
            _print_pair_scores(db, case)
            return

        cases = _list_all_cases(db)
        log.info("Diagnosing %d case(s)", len(cases))
        for c in cases:
            _print_case_fingerprint(db, c)

        # Global pair scan (each pair checked once)
        print("\n" + "=" * 80)
        print("PAIRS ABOVE PERSIST THRESHOLD (these would create CaseLink rows):")
        print("=" * 80)

        # Pre-warm embeddings if available — keeps the pair scan fast.
        if ml_models.sentence_embedder is not None:
            for c in cases:
                semsim.get_embedding(db, c)

        any_above = False
        seen = set()
        for i, a in enumerate(cases):
            fa = _build_features(db, a)
            for b in cases[i + 1:]:
                key = (a.id, b.id)
                if key in seen:
                    continue
                seen.add(key)
                fb = _build_features(db, b)
                edge = _score_pair(db, fa, fb, a, b)
                if edge.total >= MIN_PERSIST_SCORE:
                    any_above = True
                    print(f"\n  {a.case_id:>12s} ↔ {b.case_id:<12s}  "
                          f"score={edge.total:.2f}  primary={edge.primary}")
                    for name, dim in edge.dimensions.items():
                        if dim.headline_phrase:
                            print(f"      • {name}: {dim.headline_phrase}")
        if not any_above:
            print("  (none — no cases share enough signals to link)")
    finally:
        db.close()


# ════════════════════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════════════════════
def main():
    p = argparse.ArgumentParser(
        description="Case-linker maintenance — diagnose, embed, backfill",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Recommended first run:\n"
            "  python -m scripts.run_linker_backfill --full\n\n"
            "Routine maintenance after model changes:\n"
            "  python -m scripts.run_linker_backfill --embeddings --backfill\n"
        ),
    )
    p.add_argument("--diagnose",   action="store_true",
                   help="Print fingerprints + pair scores. Writes nothing.")
    p.add_argument("--embeddings", action="store_true",
                   help="Pre-compute SBERT vectors for cases missing them.")
    p.add_argument("--backfill",   action="store_true",
                   help="Run the linker on every case; write to case_links.")
    p.add_argument("--full",       action="store_true",
                   help="Shorthand for --embeddings --backfill.")
    p.add_argument("--case",       type=str, default=None,
                   help="With --diagnose: limit to one case (external id, e.g. C-2053).")
    p.add_argument("--limit",      type=int, default=None,
                   help="With --embeddings: process at most N cases.")
    p.add_argument("--batch-size", type=int, default=32,
                   help="With --embeddings: encoder batch size (default 32).")
    args = p.parse_args()

    # --full expands to --embeddings AND --backfill
    if args.full:
        args.embeddings = True
        args.backfill = True

    if not any([args.diagnose, args.embeddings, args.backfill]):
        p.print_help()
        sys.exit(1)

    # Run in a sensible order regardless of arg order on the command line:
    # 1) diagnose first (read-only, gives you a snapshot before mutating)
    # 2) embeddings second (so the linker pass can use them)
    # 3) backfill last (depends on embeddings if available)
    if args.diagnose:
        run_diagnose(case_external_id=args.case)

    if args.embeddings:
        n = run_embeddings_backfill(batch_size=args.batch_size, limit=args.limit)
        if n < 0:
            sys.exit(2)

    if args.backfill:
        run_links_backfill()


if __name__ == "__main__":
    main()