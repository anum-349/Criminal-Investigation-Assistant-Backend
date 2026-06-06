from __future__ import annotations

import logging
import struct
from typing import List, Optional

import numpy as np
from sqlalchemy.orm import Session

from models import Case, CaseEmbedding  

from fir_api.pipeline.model_loader import models as ml_models

log = logging.getLogger(__name__)

EMBED_DIM = 384  # multilingual MiniLM = 384-dim float32 vectors
MODEL_VERSION = "paraphrase-multilingual-MiniLM-L12-v2" 

def _vec_to_bytes(v: np.ndarray) -> bytes:
    if v.dtype != np.float32:
        v = v.astype(np.float32)
    return v.tobytes()


def _bytes_to_vec(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float32)

def _build_narrative(case: Case) -> str:
    """Concatenate the narrative-bearing fields into one string for
    embedding. None values are skipped; we keep field labels so the
    embedder gets weak structural cues.

    Order matters slightly (transformers weight earlier tokens more on
    short inputs), so we put the most distinctive fields first.
    """
    parts: List[str] = []
    if case.case_title:
        parts.append(f"Title: {case.case_title}")
    if case.case_type and case.case_type.label:
        parts.append(f"Type: {case.case_type.label}")
    if getattr(case, "motive", None):
        parts.append(f"Motive: {case.motive}")
    if getattr(case, "modus_operandi", None):
        parts.append(f"Modus operandi: {case.modus_operandi}")
    if case.murder_details and case.murder_details.cause_of_death:
        parts.append(f"Cause of death: {case.murder_details.cause_of_death}")
    if case.weapon and case.weapon.label:
        parts.append(f"Weapon: {case.weapon.label}")
    if getattr(case, "weapon_description", None):
        parts.append(f"Weapon details: {case.weapon_description}")
    if case.description:
        desc = case.description.strip().replace("\n", " ")
        parts.append(f"Description: {desc[:600]}")
    if getattr(case, "crime_description", None):
        parts.append(f"Crime details: {case.crime_description[:300]}")
    return " | ".join(parts).strip()

def get_embedding(db: Session, case: Case, *, persist: bool = True) -> Optional[np.ndarray]:
    """Return the 384-dim embedding for a case. Cached in the
    `case_embeddings` table; computed lazily.

    Returns None if:
      • the embedder model failed to load at app startup, OR
      • the case has no narrative text at all to embed.
    """
    embedder = ml_models.sentence_embedder
    if embedder is None:
        log.debug("SBERT not loaded; skipping semantic dim for case=%s", case.id)
        return None
 
    cached = db.query(CaseEmbedding).filter(CaseEmbedding.case_id_fk == case.id).first()
    if cached and cached.model_version == MODEL_VERSION and cached.vector:
        try:
            return _bytes_to_vec(cached.vector)
        except Exception:
            log.warning("Corrupt cached embedding for case=%s; recomputing", case.id)
            db.delete(cached)
            cached = None
 
    narrative = _build_narrative(case)
    if not narrative:
        return None
 
    try:
        vec = embedder.encode(
            narrative,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
    except Exception:
        log.exception("Embedding failed for case=%s", case.id)
        return None
 
    if vec.shape[0] != EMBED_DIM:
        log.warning("Unexpected embedding dim %s for case=%s", vec.shape, case.id)
        return None
    if vec.dtype != np.float32:
        vec = vec.astype(np.float32)
 
    if persist:
        if cached:
            cached.vector = _vec_to_bytes(vec)
            cached.model_version = MODEL_VERSION
            cached.narrative_chars = len(narrative)
        else:
            db.add(CaseEmbedding(
                case_id_fk=case.id,
                vector=_vec_to_bytes(vec),
                model_version=MODEL_VERSION,
                narrative_chars=len(narrative),
            ))
        try:
            db.flush()
        except Exception:
            log.exception("Failed to persist embedding for case=%s", case.id)
 
    return vec


def cosine_similarity(a: Optional[np.ndarray], b: Optional[np.ndarray]) -> float:
    """Cosine similarity for unit-normalised vectors = dot product.
    Returns 0.0 if either vector is missing — caller treats as 'no signal'."""
    if a is None or b is None:
        return 0.0
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def semantic_score(db: Session, case_a: Case, case_b: Case) -> float:
    """End-to-end: compute / fetch both embeddings and return cosine.
    Clamped to [0, 1] — negative cosines on multilingual MiniLM are
    essentially noise for short narratives, so we floor at 0."""
    a = get_embedding(db, case_a)
    b = get_embedding(db, case_b)
    return max(0.0, cosine_similarity(a, b))


def invalidate(db: Session, case_id: int) -> None:
    """Drop the cached embedding for one case. Call this from the linker
    hook whenever a major narrative field changes (motive, MO,
    description, weapon). The next semantic_score() recomputes."""
    (
        db.query(CaseEmbedding)
        .filter(CaseEmbedding.case_id_fk == case_id)
        .delete(synchronize_session=False)
    )

def is_available() -> bool:
    """True if the SBERT embedder is loaded and usable. Used by the
    regenerate route to log a warning when the semantic dimension
    won't fire."""
    return ml_models.sentence_embedder is not None

def backfill_embeddings(
    db: Session,
    *,
    batch_size: int = 32,
    limit: Optional[int] = None,
) -> int:
    """Pre-compute embeddings for every case that doesn't have a fresh
    one. Idempotent. Uses encode(list) for batch speedup."""
    embedder = ml_models.sentence_embedder
    if embedder is None:
        log.warning("SBERT not loaded — backfill is a no-op")
        return 0
 
    from sqlalchemy import or_
    needing = (
        db.query(Case)
        .outerjoin(CaseEmbedding, CaseEmbedding.case_id_fk == Case.id)
        .filter(
            Case.is_deleted == False,  # noqa: E712
            or_(
                CaseEmbedding.id.is_(None),
                CaseEmbedding.model_version != MODEL_VERSION,
            ),
        )
    )
    if limit is not None:
        needing = needing.limit(limit)
    cases = needing.all()
    if not cases:
        return 0
 
    log.info("Backfilling embeddings (in-process) for %d case(s)", len(cases))
    computed = 0
    for i in range(0, len(cases), batch_size):
        batch = cases[i : i + batch_size]
        narratives = [_build_narrative(c) for c in batch]
        non_empty = [(c, n) for c, n in zip(batch, narratives) if n]
        if not non_empty:
            continue
        texts = [n for _, n in non_empty]
        try:
            vecs = embedder.encode(
                texts, convert_to_numpy=True, normalize_embeddings=True,
                batch_size=batch_size, show_progress_bar=False,
            )
        except Exception:
            log.exception("Batch encode failed; skipping chunk")
            continue
 
        for (c, narrative), vec in zip(non_empty, vecs):
            if vec.dtype != np.float32:
                vec = vec.astype(np.float32)
            existing = db.query(CaseEmbedding).filter(CaseEmbedding.case_id_fk == c.id).first()
            if existing:
                existing.vector = _vec_to_bytes(vec)
                existing.model_version = MODEL_VERSION
                existing.narrative_chars = len(narrative)
            else:
                db.add(CaseEmbedding(
                    case_id_fk=c.id,
                    vector=_vec_to_bytes(vec),
                    model_version=MODEL_VERSION,
                    narrative_chars=len(narrative),
                ))
            computed += 1
 
        try:
            db.commit()
        except Exception:
            log.exception("Commit failed during backfill batch")
            db.rollback()
 
    log.info("Backfill complete: %d embedding(s) written", computed)
    return computed