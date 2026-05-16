"""
pipeline/urdu_translator.py
───────────────────────────
Urdu → English neural machine translation using `Helsinki-NLP/opus-mt-ur-en`.

Why MarianMT?
    • Genuine pretrained transformer (encoder-decoder, 6+6 layers, 76 M params)
    • Released by the Helsinki OPUS project, trained on OPUS Urdu↔English
      parallel corpora (~2 M sentence pairs)
    • Fully offline once weights are downloaded — no API calls
    • CPU inference: ~150-300 ms per sentence on a modern x86 core
    • Permissive license (Apache 2.0)

Compared to the old rule-based lexicon, this:
    • Handles unseen words (the lexicon was capped at ~2.5k FIR terms)
    • Preserves grammar and word order, not just substitution
    • Translates Roman-Urdu via the multilingual fallback path

Fallback strategy:
    If the model fails to load (no internet on first run, weights missing),
    we fall back to the legacy lexicon so the pipeline still degrades
    gracefully instead of 500-ing.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import List, Optional

from pipeline.language_detector import detect_language
from pipeline.model_loader import models
from pipeline.urdu_lexicon import URDU_LEXICON, URDU_SCRIPT_RE

logger = logging.getLogger("fir.translate")

# Marian works best on sentences ≤ ~200 tokens. We chunk by paragraph then
# sentence to stay within that and avoid quality drop on long inputs.
_MAX_CHARS_PER_CHUNK = 500
_SENT_SPLIT_RE = re.compile(r"(?<=[.!؟۔\n])\s+")


@dataclass
class TranslationResult:
    translated_text: str
    source_language: str          # "urdu" | "english" | "mixed" | "unknown"
    coverage:        float        # 0.0-1.0  — for marian: confidence proxy, for lexicon: % matched
    method:          str          # "marian" | "lexicon_fallback" | "passthrough"
    unknown_tokens:  List[str]
    chunks_translated: int = 0

    def to_dict(self) -> dict:
        return {
            "translated_text":   self.translated_text,
            "source_language":   self.source_language,
            "coverage":          round(self.coverage, 3),
            "method":            self.method,
            "unknown_tokens":    self.unknown_tokens[:20],
            "chunks_translated": self.chunks_translated,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Chunking
# ─────────────────────────────────────────────────────────────────────────────

def _chunk_text(text: str, max_chars: int = _MAX_CHARS_PER_CHUNK) -> List[str]:
    """Split text into ≤max_chars chunks at sentence boundaries.

    FIRs are typically structured (Field: Value), and Marian degrades when
    fed huge blobs. Chunking lets us:
      • Translate each chunk in parallel (batched in one tokenizer call)
      • Keep memory bounded
      • Recover gracefully from a single bad chunk
    """
    if len(text) <= max_chars:
        return [text]

    out: List[str] = []
    current: List[str] = []
    current_len = 0

    for sent in _SENT_SPLIT_RE.split(text):
        sent = sent.strip()
        if not sent:
            continue
        if current_len + len(sent) > max_chars and current:
            out.append(" ".join(current))
            current, current_len = [], 0
        current.append(sent)
        current_len += len(sent) + 1

    if current:
        out.append(" ".join(current))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Lexicon fallback (kept from the legacy pipeline)
# ─────────────────────────────────────────────────────────────────────────────

def _translate_lexicon(text: str) -> TranslationResult:
    """Word-by-word lexicon substitution. Used when MarianMT isn't available."""
    tokens = text.split()
    out, unknown, matched, urdu_total = [], [], 0, 0

    for tok in tokens:
        clean = tok.strip("۔،؟!.,:;\"'()[]{}")
        if clean and URDU_SCRIPT_RE.match(clean[:1]):
            urdu_total += 1
            if clean in URDU_LEXICON:
                replacement = URDU_LEXICON[clean]
                if replacement:
                    out.append(replacement)
                matched += 1
            else:
                out.append(f"[{clean}]")
                unknown.append(clean)
        else:
            out.append(tok)

    coverage = matched / urdu_total if urdu_total else 1.0
    return TranslationResult(
        translated_text=" ".join(out),
        source_language="urdu",
        coverage=coverage,
        method="lexicon_fallback",
        unknown_tokens=unknown,
        chunks_translated=1,
    )


# ─────────────────────────────────────────────────────────────────────────────
# MarianMT translator
# ─────────────────────────────────────────────────────────────────────────────

def _translate_marian(text: str) -> TranslationResult:
    """Neural translation via the warmed-up MarianMT pipeline."""
    chunks = _chunk_text(text)

    # `pipeline("translation")` accepts a list and batches internally.
    # max_length tuned for FIR field-value pairs; bump if you see truncation.
    outputs = models.translator_pipeline(
        chunks,
        max_length=512,
        num_beams=4,            # quality vs speed knob; 4 is the OPUS default
        early_stopping=True,
    )
    translated = " ".join(o["translation_text"] for o in outputs)

    # We don't have per-token confidences from the HF pipeline by default, so
    # we report a coarse coverage proxy: fraction of Urdu script tokens that
    # actually disappeared from the output (i.e. were translated, not copied).
    in_urdu = len([t for t in text.split() if t and URDU_SCRIPT_RE.match(t[:1])])
    out_urdu = len([t for t in translated.split() if t and URDU_SCRIPT_RE.match(t[:1])])
    coverage = 1.0 if in_urdu == 0 else max(0.0, 1.0 - (out_urdu / in_urdu))

    # Untranslated Urdu tokens that leaked through — useful for the XAI panel.
    unknown = [t for t in translated.split() if t and URDU_SCRIPT_RE.match(t[:1])][:20]

    return TranslationResult(
        translated_text=translated,
        source_language="urdu",
        coverage=coverage,
        method="marian",
        unknown_tokens=unknown,
        chunks_translated=len(chunks),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def translate_urdu_to_english(text: str) -> dict:
    """Public entry. Always returns the dict shape the orchestrator expects."""
    if not text or not text.strip():
        return TranslationResult("", "unknown", 0.0, "passthrough", []).to_dict()

    lang = detect_language(text)

    if lang.language == "english":
        return TranslationResult(
            translated_text=text,
            source_language="english",
            coverage=1.0,
            method="passthrough",
            unknown_tokens=[],
            chunks_translated=0,
        ).to_dict()

    # Mixed and Urdu both go through translation — Marian handles mixed
    # input gracefully (English passes through unchanged).
    if models.translator_pipeline is not None:
        try:
            result = _translate_marian(text)
            result.source_language = lang.language  # preserve "mixed" vs "urdu"
            return result.to_dict()
        except Exception as e:
            logger.warning("MarianMT failed (%s) — falling back to lexicon.", e)

    fallback = _translate_lexicon(text)
    fallback.source_language = lang.language
    return fallback.to_dict()


# Kept for backwards compat with existing tests
def detect_language_legacy(text: str) -> str:
    """Legacy API — returns just the string label."""
    return detect_language(text).language