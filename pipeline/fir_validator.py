"""
fir_validator.py
────────────────
Determines whether extracted text is actually a First Information Report (FIR).

Uses a lightweight TF-IDF + Logistic Regression classifier trained on:
  • Positive patterns: FIR-specific keywords, section numbers, police terminology.
  • Negative patterns: generic documents (invoices, news, academic text, etc.)

Because we have no labelled dataset at install time, the classifier is
rule-seeded: it converts keyword signal into training rows, then fits.
This means it works fully offline with no corpus download.

The validator also provides XAI (Explainable AI) output:
  • Which keywords triggered a positive / negative signal.
  • A per-keyword confidence weight.
  • A human-readable reason string.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

# ──────────────────────────────────────────────────────────────────────────────
# Keyword seed sets  (used to synthesise training data)
# ──────────────────────────────────────────────────────────────────────────────

# FIR-positive signals
_POSITIVE_SEEDS = [
    "first information report", "fir", "fir number", "case number",
    "police station", "station house officer", "sho", "complainant",
    "accused", "suspect", "victim", "witness", "offence", "crime",
    "section", "pakistan penal code", "ppc", "crpc",
    "investigation", "arrest", "custody", "bail", "charge sheet",
    "murder", "robbery", "theft", "kidnapping", "assault", "fraud",
    "district", "province", "weapon", "pistol", "rifle", "knife",
    "vehicle registration", "motorcycle", "number plate",
    "date of incident", "time of incident", "place of incident",
    "complainant name", "father name", "cnic", "national identity",
    "registered fir", "maqam waqia", "mukadma", "ملزم", "مدعی",
    "تھانہ", "ایف آئی آر", "مقدمہ", "دفعہ", "گرفتاری",
]

# FIR-negative signals (other document types)
_NEGATIVE_SEEDS = [
    "invoice", "total amount", "payment due", "tax", "gst",
    "abstract", "methodology", "conclusion", "bibliography",
    "chapter", "introduction", "literature review",
    "news article", "editor", "published", "subscription",
    "profit", "loss", "revenue", "quarterly earnings",
    "recipe", "ingredients", "cooking", "bake",
    "weather forecast", "temperature", "humidity",
]


# ──────────────────────────────────────────────────────────────────────────────
# Synthetic training data builder
# ──────────────────────────────────────────────────────────────────────────────

def _build_training_data() -> Tuple[List[str], List[int]]:
    """
    Build a tiny labelled corpus from seed phrases by mixing them.
    Positive class = 1 (FIR),  Negative class = 0 (not FIR).
    """
    texts, labels = [], []

    # Positive: single seeds + multi-phrase combinations
    for seed in _POSITIVE_SEEDS:
        texts.append(seed)
        labels.append(1)

    # Multi-keyword positive docs
    for i in range(0, len(_POSITIVE_SEEDS) - 2, 3):
        combo = " ".join(_POSITIVE_SEEDS[i: i + 3])
        texts.append(combo)
        labels.append(1)

    # Negative
    for seed in _NEGATIVE_SEEDS:
        texts.append(seed)
        labels.append(0)

    for i in range(0, len(_NEGATIVE_SEEDS) - 2, 3):
        combo = " ".join(_NEGATIVE_SEEDS[i: i + 3])
        texts.append(combo)
        labels.append(0)

    return texts, labels


# ──────────────────────────────────────────────────────────────────────────────
# Classifier (trained once at module load)
# ──────────────────────────────────────────────────────────────────────────────

_texts, _labels = _build_training_data()
_CLASSIFIER: Pipeline = Pipeline([
    ("tfidf", TfidfVectorizer(
        ngram_range=(1, 3),
        max_features=5000,
        sublinear_tf=True,
        min_df=1,
    )),
    ("lr", LogisticRegression(C=5.0, max_iter=500, random_state=42)),
])
_CLASSIFIER.fit(_texts, _labels)

# Grab vocabulary for XAI
_VOCAB: Dict[str, int] = _CLASSIFIER.named_steps["tfidf"].vocabulary_
_COEFS: np.ndarray = _CLASSIFIER.named_steps["lr"].coef_[0]  # shape (n_features,)


# ──────────────────────────────────────────────────────────────────────────────
# XAI: keyword-level evidence
# ──────────────────────────────────────────────────────────────────────────────

def _explain(text: str) -> List[Dict]:
    """
    Return top features (words/phrases) that most influenced the decision.
    Each item: { term, weight, direction: 'positive'|'negative' }
    Positive direction → pushes toward FIR; negative → pushes away.
    """
    vec = _CLASSIFIER.named_steps["tfidf"].transform([text])
    # Sparse × coef elementwise
    scores = np.asarray(vec.multiply(_COEFS).todense()).flatten()
    top_idx = np.argsort(np.abs(scores))[::-1][:15]

    inv_vocab = {v: k for k, v in _VOCAB.items()}
    evidence = []
    for idx in top_idx:
        if scores[idx] == 0:
            continue
        term = inv_vocab.get(idx, "?")
        # Only report terms that actually appear in the text
        if term.lower() in text.lower():
            evidence.append({
                "term": term,
                "weight": round(float(scores[idx]), 4),
                "direction": "positive" if scores[idx] > 0 else "negative",
            })
    return evidence


# ──────────────────────────────────────────────────────────────────────────────
# Public result dataclass
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    is_fir: bool
    confidence: float                    # 0.0 – 1.0
    reason: str
    evidence: List[Dict] = field(default_factory=list)
    missing_critical: List[str] = field(default_factory=list)

    def to_dict(self):
        return {
            "is_fir": self.is_fir,
            "confidence": self.confidence,
            "reason": self.reason,
            "evidence": self.evidence,
            "missing_critical": self.missing_critical,
        }


# ──────────────────────────────────────────────────────────────────────────────
# Critical fields heuristic check
# ──────────────────────────────────────────────────────────────────────────────

_CRITICAL_PATTERNS = {
    "FIR Number":         re.compile(r"(fir|case|maqdam|mukadma)[\s\-#:]*\d+", re.I),
    "Complainant Name":   re.compile(r"(complainant|maddai|شاکی|مدعی)[:\s]+\w+", re.I),
    "Accused / Suspect":  re.compile(r"(accused|suspect|ملزم|مشتبہ)[:\s]+\w+", re.I),
    "Date of Incident":   re.compile(
        r"\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}|"
        r"(january|february|march|april|may|june|july|august|"
        r"september|october|november|december)\s+\d{1,2}", re.I),
    "Police Station":     re.compile(r"(police station|thana|تھانہ)[:\s]+\w+", re.I),
    "Section / Offence":  re.compile(r"(section|dafaa|دفعہ)\s+\d+", re.I),
}


def check_missing_fields(text: str) -> List[str]:
    missing = []
    for field_name, pattern in _CRITICAL_PATTERNS.items():
        if not pattern.search(text):
            missing.append(field_name)
    return missing


# ──────────────────────────────────────────────────────────────────────────────
# Main validation function
# ──────────────────────────────────────────────────────────────────────────────

CONFIDENCE_THRESHOLD = 0.45   # below this → not a FIR


def validate_fir(text: str) -> ValidationResult:
    """
    Determine if `text` is a valid FIR document.

    Returns a ValidationResult with XAI evidence.
    """
    if not text or len(text.strip()) < 50:
        return ValidationResult(
            is_fir=False,
            confidence=0.0,
            reason="Text too short to analyse. Ensure the file is readable.",
        )

    proba = _CLASSIFIER.predict_proba([text])[0]
    fir_prob = float(proba[1])
    evidence = _explain(text)
    missing = check_missing_fields(text)

    # Build human-readable reason
    pos_terms = [e["term"] for e in evidence if e["direction"] == "positive"][:5]
    neg_terms = [e["term"] for e in evidence if e["direction"] == "negative"][:3]

    if fir_prob >= CONFIDENCE_THRESHOLD:
        reason = (
            f"Document classified as FIR ({fir_prob:.0%} confidence). "
            f"Key indicators found: {', '.join(pos_terms) if pos_terms else 'general police/legal terminology'}."
        )
        if missing:
            reason += f" Missing fields: {', '.join(missing)}."
        return ValidationResult(
            is_fir=True,
            confidence=round(fir_prob, 4),
            reason=reason,
            evidence=evidence,
            missing_critical=missing,
        )
    else:
        reason = (
            f"Document does not appear to be a FIR ({fir_prob:.0%} FIR confidence). "
        )
        if neg_terms:
            reason += f"Non-FIR signals detected: {', '.join(neg_terms)}. "
        reason += "Please upload a valid First Information Report document."
        return ValidationResult(
            is_fir=False,
            confidence=round(fir_prob, 4),
            reason=reason,
            evidence=evidence,
            missing_critical=[],
        )