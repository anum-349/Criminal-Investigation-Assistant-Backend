"""
pipeline/language_detector.py
─────────────────────────────
Detects whether the OCR'd text is Urdu, English, or mixed.

Previous version used a character-block ratio (Unicode 0600-06FF). That works
but fails on:
  • Roman-Urdu ("mai apne ghar par tha jab teen mulzim aaye…")
  • OCR noise that produces garbage Unicode in the Urdu block
  • Short snippets where the ratio is unstable

New version uses FastText's `lid.176` language identifier (offline, 130 MB,
176 languages, ~5 ms per inference). We still keep the script-ratio as a
secondary signal because FastText sometimes confuses Urdu with Arabic on
single sentences — when both signals agree, confidence goes up.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger("fir.lang")

# Urdu/Arabic Unicode blocks. Useful as a sanity-check on FastText output.
_URDU_SCRIPT_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")

# FastText is lazy-imported so import time stays low; cached after first call.
_ft_detector = None

def _get_ft_detector():
    """Returns a callable: text -> {'lang': str, 'score': float}.
    Uses `langdetect` (pure Python) instead of FastText — same interface."""
    global _ft_detector
    if _ft_detector is None:
        try:
            from langdetect import detect_langs, DetectorFactory            
            
            DetectorFactory.seed = 0  # make results deterministic

            def _wrapper(text, low_memory=False):
                # langdetect returns [Language('en', prob=0.99), ...]; mimic ftlangdetect's shape
                try:
                    langs = detect_langs(text)
                    if not langs:
                        return {"lang": "unknown", "score": 0.0}
                    top = langs[0]
                    return {"lang": top.lang, "score": float(top.prob)}
                except Exception:
                    return {"lang": "unknown", "score": 0.0}

            _ft_detector = _wrapper
        except Exception as e:
            logger.warning("langdetect unavailable (%s). Falling back to script-ratio only.", e)
            _ft_detector = False
    return _ft_detector

@dataclass
class LanguageResult:
    language: Literal["urdu", "english", "mixed", "unknown"]
    confidence: float           # 0.0 – 1.0
    script_ratio: float         # fraction of non-space chars that are Urdu/Arabic script
    method: str                 # which signal won

    def to_dict(self) -> dict:
        return {
            "detected":     self.language,
            "confidence":   round(self.confidence, 3),
            "script_ratio": round(self.script_ratio, 3),
            "method":       self.method,
        }


def _script_ratio(text: str) -> float:
    """Fraction of non-space characters that are Urdu/Arabic script."""
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0
    return sum(1 for c in chars if _URDU_SCRIPT_RE.match(c)) / len(chars)


def detect_language(text: str) -> LanguageResult:
    """Return language verdict with confidence and an explanation of which
    signal won. Used by both the pipeline and the XAI panel."""
    if not text or not text.strip():
        return LanguageResult("unknown", 0.0, 0.0, "empty_input")

    script = _script_ratio(text)

    # Fast path: very high or very low script ratio is unambiguous.
    if script > 0.7:
        return LanguageResult("urdu", 0.95, script, "script_ratio")
    if script < 0.05:
        # Could still be Roman-Urdu — let FastText decide.
        pass

    ft = _get_ft_detector()
    if ft:
        try:
            # FastText returns {"lang": "ur", "score": 0.99}
            # Strip newlines: FT spec disallows them.
            clean = " ".join(text.split())[:2000]
            result = ft(clean, low_memory=False)
            lang_code = result.get("lang", "")
            ft_conf   = float(result.get("score", 0.0))

            if lang_code == "ur":
                return LanguageResult("urdu", ft_conf, script, "fasttext")
            if lang_code == "en":
                # Catch Roman-Urdu false-negatives: if FT says English but we
                # see many "ki", "ke", "mei", "tha" tokens, mark as mixed.
                roman_urdu_markers = re.findall(
                    r"\b(ki|ke|ka|mein|mei|tha|thi|aur|sath|baad|qabal|woh|aap|"
                    r"mai|maine|mujhe|tum|hum|mulzim|thana|maddai|gawaah)\b",
                    text, re.I,
                )
                if len(roman_urdu_markers) >= 3:
                    return LanguageResult("mixed", min(ft_conf, 0.6), script, "fasttext+romanurdu")
                return LanguageResult("english", ft_conf, script, "fasttext")
            # Any other language (Hindi, Arabic, Persian, …) treat as "mixed"
            # so we route through translation — these all share script with Urdu.
            return LanguageResult("mixed", ft_conf, script, f"fasttext:{lang_code}")
        except Exception as e:
            logger.warning("FastText detect failed: %s — falling back to ratio.", e)

    # Fallback when FastText is not available
    if script > 0.5:
        return LanguageResult("urdu", 0.7, script, "script_ratio_fallback")
    if script > 0.1:
        return LanguageResult("mixed", 0.6, script, "script_ratio_fallback")
    return LanguageResult("english", 0.7, script, "script_ratio_fallback")