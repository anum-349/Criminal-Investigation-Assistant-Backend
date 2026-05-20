"""
scripts/backfill_embeddings.py
─────────────────────────────────────────────────────────────────────────────
Pre-compute SBERT embeddings for every case that doesn't have a fresh one.

WHEN TO RUN
───────────
  • Once, after applying the case_embeddings migration.
  • After upgrading the embedder model (the MODEL_VERSION constant in
    services/semantic_similarity.py).
  • As a routine maintenance script — it's idempotent and only computes
    rows that are missing or stale.

USAGE
─────
  python -m scripts.backfill_embeddings              # all cases
  python -m scripts.backfill_embeddings --limit 100  # try a sample first
  python -m scripts.backfill_embeddings --dry-run    # show counts only

PERFORMANCE
───────────
MiniLM on CPU encodes ~30 narratives/sec. For 1000 cases, expect ~30s.
The script batches encode() calls to maximise throughput.
"""
from __future__ import annotations

import argparse
import logging
import sys

# Adjust import path if your project layout differs.
from db import SessionLocal
from services import semantic_similarity as semsim
from fir_api.pipeline import models as ml_models
from fir_api.pipeline.model_loader import _load_embedder  # explicit re-init if missing


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
)
log = logging.getLogger("backfill-embeddings")


def main():
    p = argparse.ArgumentParser(description="Pre-compute SBERT embeddings for all cases")
    p.add_argument("--limit", type=int, default=None,
                   help="Process at most N cases (default: all)")
    p.add_argument("--batch-size", type=int, default=32,
                   help="Encoder batch size (default 32)")
    p.add_argument("--dry-run", action="store_true",
                   help="Count what would be computed; don't write")
    args = p.parse_args()

    # Make sure the embedder is loaded. If you run this script from a
    # standalone Python process, the FastAPI startup hook hasn't fired,
    # so we trigger it manually.
    if ml_models.sentence_embedder is None:
        log.info("Embedder not loaded; loading now…")
        _load_embedder()

    if ml_models.sentence_embedder is None:
        log.error("Embedder failed to load. Check fir_api/pipeline/model_loader.py logs.")
        sys.exit(1)

    db = SessionLocal()
    try:
        if args.dry_run:
            from models import Case, CaseEmbedding
            from sqlalchemy import or_
            total = (
                db.query(Case)
                .outerjoin(CaseEmbedding, CaseEmbedding.case_id_fk == Case.id)
                .filter(
                    Case.is_deleted == False,  # noqa: E712
                    or_(
                        CaseEmbedding.id.is_(None),
                        CaseEmbedding.model_version != semsim.MODEL_VERSION,
                    ),
                )
                .count()
            )
            log.info("DRY RUN: %d case(s) need embeddings (model_version=%s)",
                     total, semsim.MODEL_VERSION)
            return

        computed = semsim.backfill_embeddings(
            db,
            batch_size=args.batch_size,
            limit=args.limit,
        )
        log.info("Done. Computed %d embedding(s).", computed)
    finally:
        db.close()


if __name__ == "__main__":
    main()