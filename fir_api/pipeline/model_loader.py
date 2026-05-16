"""
pipeline/model_loader.py
────────────────────────
Singleton registry for all AI models used in the pipeline.

Why a singleton?
    Transformers are slow to load (200-500 MB on disk, 2-8 s cold start).
    If we load them per-request, the API is unusable. We load once at
    process startup and keep references warm for the lifetime of the worker.

Industry practice:
    • Models are loaded eagerly in `warmup()` — called from FastAPI's
      `@app.on_event("startup")` hook so the first real request is fast.
    • Each loader is wrapped in a try/except so the API still boots even if
      one model fails (we log a warning and fall back to lighter logic).
    • Model cache directory is controlled by HF_HOME env var so Docker can
      bake weights into the image (avoiding download on container start).

Models loaded:
    1. MarianMT  (Helsinki-NLP/opus-mt-ur-en)         — Urdu → English MT
    2. spaCy     (en_core_web_sm)                     — English NER
    3. HF NER    (mirfan899/uner-uner-mbert)          — Urdu NER (mBERT fine-tuned)
    4. SBERT     (paraphrase-multilingual-MiniLM-L12) — multilingual sentence embeddings
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("fir.models")
logger.setLevel(logging.INFO)


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

# Model identifiers — pinned for reproducibility.
# Override via env vars if you want to swap in fine-tuned weights later.
MARIAN_UR_EN_MODEL = os.getenv("FIR_TRANSLATION_MODEL", "Helsinki-NLP/opus-mt-ur-en")
SBERT_MODEL        = os.getenv("FIR_EMBEDDING_MODEL",   "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
URDU_NER_MODEL     = os.getenv("FIR_URDU_NER_MODEL",    "mirfan899/uner-uner-mbert")
SPACY_EN_MODEL     = os.getenv("FIR_SPACY_EN_MODEL",    "en_core_web_sm")

# Where transformers cache weights. Set this in Dockerfile so we bake models
# into the image and don't need internet at runtime.
HF_CACHE = os.getenv("HF_HOME", "/root/.cache/huggingface")


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ModelRegistry:
    """Holds every loaded model. Access via the global `models` instance."""
    translator_pipeline: Optional[Any] = None   # transformers pipeline("translation")
    sentence_embedder:   Optional[Any] = None   # SentenceTransformer
    urdu_ner_pipeline:   Optional[Any] = None   # transformers pipeline("ner")
    spacy_nlp:           Optional[Any] = None   # spacy.Language

    # FIR classifier head — fitted at startup on top of sentence embeddings.
    # See fir_validator.py for the training corpus.
    fir_classifier:      Optional[Any] = None   # sklearn LogisticRegression

    # Health flags
    status: dict = field(default_factory=dict)

    @property
    def all_ready(self) -> bool:
        return all([
            self.translator_pipeline is not None,
            self.sentence_embedder   is not None,
            self.spacy_nlp           is not None,
            self.fir_classifier      is not None,
        ])


models = ModelRegistry()
_warmup_lock = threading.Lock()
_warmup_done = False


# ─────────────────────────────────────────────────────────────────────────────
# Individual loaders
# ─────────────────────────────────────────────────────────────────────────────

def _load_translator() -> None:
    """Helsinki-NLP/opus-mt-ur-en — Urdu→English Marian MT model.

    Size: ~310 MB. CPU inference: ~150 ms per sentence.
    Pre-downloaded in Docker layer so no network needed at runtime.
    """
    try:
        from transformers import pipeline, MarianMTModel, MarianTokenizer

        logger.info("Loading translator: %s", MARIAN_UR_EN_MODEL)
        tokenizer = MarianTokenizer.from_pretrained(MARIAN_UR_EN_MODEL, cache_dir=HF_CACHE)
        model     = MarianMTModel.from_pretrained(MARIAN_UR_EN_MODEL, cache_dir=HF_CACHE)
        models.translator_pipeline = pipeline(
            "translation",
            model=model,
            tokenizer=tokenizer,
            device=-1,  # CPU. Set to 0 if you have a GPU.
        )
        models.status["translator"] = "ready"
        logger.info("✓ Translator loaded")
    except Exception as e:
        logger.exception("Translator failed to load — falling back to lexicon")
        models.status["translator"] = f"error: {e}"
        models.translator_pipeline = None


def _load_embedder() -> None:
    """Multilingual MiniLM — 384-dim embeddings for Urdu, English, mixed.

    Size: ~120 MB. We use this for both:
      • FIR-vs-not-FIR classification (downstream LogReg head)
      • Future: case similarity / lead generation
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
        logger.exception("Embedder failed to load")
        models.status["embedder"] = f"error: {e}"
        models.sentence_embedder = None


def _load_urdu_ner() -> None:
    """Urdu NER model — mBERT fine-tuned for Urdu PER/LOC/ORG tags.

    Optional. If unavailable, we'll rely on translating to English then
    running spaCy NER. Loading it lets us extract entities directly from
    Urdu text without losing information in translation (recommended).
    """
    try:
        from transformers import AutoTokenizer, AutoModelForTokenClassification, pipeline

        logger.info("Loading Urdu NER: %s", URDU_NER_MODEL)
        tokenizer = AutoTokenizer.from_pretrained(URDU_NER_MODEL, cache_dir=HF_CACHE)
        model     = AutoModelForTokenClassification.from_pretrained(URDU_NER_MODEL, cache_dir=HF_CACHE)
        models.urdu_ner_pipeline = pipeline(
            "ner",
            model=model,
            tokenizer=tokenizer,
            aggregation_strategy="simple",  # merges B-PER + I-PER into single span
            device=-1,
        )
        models.status["urdu_ner"] = "ready"
        logger.info("✓ Urdu NER loaded")
    except Exception as e:
        # This one is optional — log a warning, not an error.
        logger.warning("Urdu NER not available (%s). Will translate first then use spaCy.", e)
        models.status["urdu_ner"] = f"unavailable: {e}"
        models.urdu_ner_pipeline = None


def _load_spacy() -> None:
    """spaCy English pipeline — used on translated text for general NER
    (PERSON, GPE, ORG) and dependency parsing of complainant/accused phrases.

    We use the small model (`en_core_web_sm`, ~13 MB). For higher accuracy
    swap to `en_core_web_trf` (transformer, ~440 MB) — but trf needs more
    RAM and quadruples the cold-start time.
    """
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
    """Train the FIR-vs-not-FIR head on top of sentence embeddings.

    We *could* fine-tune a transformer, but for a binary domain decision a
    LogReg on top of frozen multilingual embeddings is:
      • 100x faster to train (seconds, not hours)
      • 1000x faster to serve (no backprop, no extra forward pass)
      • More than accurate enough — published benchmarks put SBERT+LR at
        93-96% on short-text binary classification.

    The training corpus is intentionally small and curated — see
    `fir_validator._build_training_corpus()`. It covers Urdu, English, and
    Roman-Urdu phrasings of FIR/non-FIR documents.
    """
    if models.sentence_embedder is None:
        logger.warning("Skipping FIR classifier — embedder not loaded.")
        models.status["fir_classifier"] = "skipped: no embedder"
        return

    try:
        from sklearn.linear_model import LogisticRegression
        from pipeline.fir_validator import _build_training_corpus

        texts, labels = _build_training_corpus()
        logger.info("Encoding %d training docs for FIR classifier…", len(texts))
        X = models.sentence_embedder.encode(texts, batch_size=32, show_progress_bar=False)

        clf = LogisticRegression(C=2.0, max_iter=2000, random_state=42)
        clf.fit(X, labels)
        models.fir_classifier = clf

        # Cheap self-check: training accuracy should be >95% on this corpus.
        train_acc = clf.score(X, labels)
        models.status["fir_classifier"] = f"ready (train_acc={train_acc:.3f})"
        logger.info("✓ FIR classifier fitted (train_acc=%.3f)", train_acc)
    except Exception as e:
        logger.exception("FIR classifier fit failed")
        models.status["fir_classifier"] = f"error: {e}"
        models.fir_classifier = None


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def warmup() -> dict:
    """Load every model. Idempotent — safe to call multiple times.

    Returns the status dict so the /health endpoint can report readiness.
    Called from `api/main.py`'s startup hook.
    """
    global _warmup_done
    with _warmup_lock:
        if _warmup_done:
            return models.status

        logger.info("═" * 60)
        logger.info("FIR pipeline cold-start warmup")
        logger.info("═" * 60)

        _load_spacy()           # cheap, do first
        _load_embedder()        # needed by classifier
        _fit_fir_classifier()   # needs embedder
        _load_translator()      # heavy
        _load_urdu_ner()        # optional, do last so failure here doesn't block

        _warmup_done = True
        logger.info("Warmup complete: %s", models.status)
        return models.status


def is_ready() -> bool:
    """Lightweight readiness check used by /health and load balancers."""
    return _warmup_done and models.all_ready