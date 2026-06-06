from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("fir.models")
logger.setLevel(logging.INFO)

_BASE       = os.path.dirname(os.path.abspath(__file__))
_MODELS_DIR = os.path.join(_BASE, "..", "models")

SBERT_MODEL    = os.getenv("FIR_EMBEDDING_MODEL", os.path.join(_MODELS_DIR, "paraphrase-multilingual-MiniLM-L12-v2"))
SPACY_EN_MODEL = os.getenv("FIR_SPACY_EN_MODEL",  "en_core_web_sm")
HF_CACHE       = os.getenv("HF_HOME", "/root/.cache/huggingface")

@dataclass
class ModelRegistry:
    """Holds every loaded model. Access via the global `models` instance."""
    sentence_embedder: Optional[Any] = None   # SentenceTransformer
    spacy_nlp:         Optional[Any] = None   # spacy.Language
    fir_classifier:    Optional[Any] = None   # sklearn LogisticRegression

    status: dict = field(default_factory=dict)

    @property
    def all_ready(self) -> bool:
        return all([
            self.sentence_embedder is not None,
            self.spacy_nlp         is not None,
            self.fir_classifier    is not None,
        ])


models = ModelRegistry()
_warmup_lock = threading.Lock()
_warmup_done = False

def _load_embedder() -> None:
    """Multilingual MiniLM — 384-dim embeddings.

    Used by:
      • backfill_embeddings  — stores per-FIR vectors in the DB
      • semantic_similarity  — cosine similarity between FIRs
      • run_backfiller       — batch wrapper around backfill_embeddings
      • fir_classifier       — LogReg head for FIR validation
    """
    try:
        from sentence_transformers import SentenceTransformer

        logger.info("Loading sentence embedder: %s", SBERT_MODEL)
        models.sentence_embedder = SentenceTransformer(
            SBERT_MODEL, cache_folder=HF_CACHE, device="cpu",
        )
        models.status["embedder"] = "ready"
        logger.info("✓ Sentence embedder loaded")
    except Exception as e:
        logger.exception("SBERT failed to load: %s", e)
        models.status["embedder"] = f"error: {e}"
        models.sentence_embedder = None


def _load_spacy() -> None:
    """spaCy English NER — used by entity_extractor.py on translated/English text."""
    try:
        import spacy

        logger.info("Loading spaCy: %s", SPACY_EN_MODEL)
        models.spacy_nlp = spacy.load(SPACY_EN_MODEL)
        models.status["spacy"] = "ready"
        logger.info("✓ spaCy loaded")
    except Exception as e:
        logger.exception("spaCy failed to load")
        models.status["spacy"] = f"error: {e}"
        models.spacy_nlp = None


def _fit_fir_classifier() -> None:
    """LogReg FIR-vs-not-FIR head on top of sentence embeddings.

    Still used by fir_validator.py as a lightweight sanity check
    even though the main translation/extraction is now done by Groq.
    """
    if models.sentence_embedder is None:
        logger.warning("Skipping FIR classifier — embedder not loaded.")
        models.status["fir_classifier"] = "skipped: no embedder"
        return

    try:
        from sklearn.linear_model import LogisticRegression
        from fir_api.pipeline.fir_validator import _build_training_corpus

        texts, labels = _build_training_corpus()
        logger.info("Encoding %d training docs for FIR classifier…", len(texts))
        X = models.sentence_embedder.encode(texts, batch_size=32, show_progress_bar=False)

        clf = LogisticRegression(C=2.0, max_iter=2000, random_state=42)
        clf.fit(X, labels)
        models.fir_classifier = clf

        train_acc = clf.score(X, labels)
        models.status["fir_classifier"] = f"ready (train_acc={train_acc:.3f})"
        logger.info("✓ FIR classifier fitted (train_acc=%.3f)", train_acc)
    except Exception as e:
        logger.exception("FIR classifier fit failed")
        models.status["fir_classifier"] = f"error: {e}"
        models.fir_classifier = None

def warmup() -> dict:
    """Load every remaining model. Idempotent — safe to call multiple times.

    What changed vs the original:
      • _load_translator()  removed — Groq/Llama handles Urdu→English
      • _load_urdu_ner()    removed — entity_extractor uses spaCy on Llama output
      • Everything else is unchanged so backfill_embeddings / semantic_similarity
        / run_backfiller continue to work without modification.
    """
    global _warmup_done
    with _warmup_lock:
        if _warmup_done:
            return models.status

        logger.info("=" * 60)
        logger.info("FIR pipeline warmup (slim — Groq edition)")
        logger.info("=" * 60)

        _load_spacy()            # cheap, do first
        _load_embedder()         # needed by classifier + embedding consumers
        _fit_fir_classifier()    # needs embedder

        _warmup_done = True
        logger.info("Warmup complete: %s", models.status)
        return models.status


def is_ready() -> bool:
    return _warmup_done and models.all_ready