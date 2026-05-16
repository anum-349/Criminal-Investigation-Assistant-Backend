"""
pipeline/fir_validator.py
─────────────────────────
Determines whether the uploaded document is a First Information Report.

Architecture:
    Text  →  multilingual SBERT (frozen)  →  LogReg head  →  P(is_FIR)

Why this beats the old TF-IDF + LR:
    • SBERT understands *semantics*, not just word overlap. A FIR that uses
      synonyms ("complainant" vs "petitioner", "incident" vs "occurrence")
      is still recognised. TF-IDF treats them as unrelated.
    • Multilingual SBERT works directly on Urdu, English, and Roman-Urdu —
      no translation step needed for validation, so we catch non-FIR
      Urdu documents before spending CPU on translation.
    • XAI: we keep keyword-evidence + LIME (now on real embeddings) so the
      reasoning panel still makes sense to investigators.

Industry practices applied here:
    • Frozen embeddings + linear head = fast training, fast serving, robust
      to small training corpora (Bommasani et al., 2021)
    • Class-balanced synthetic corpus seeded with real FIR phrasings in
      both languages
    • Threshold tuned via cross-validation, not hard-coded at 0.5
    • Decision returns a *calibrated* probability + missing-field heuristic
      so downstream code can show a uniform confidence to the user
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from pipeline.model_loader import models

logger = logging.getLogger("fir.validator")

CONFIDENCE_THRESHOLD = 0.55  # tuned on the synthetic CV set; see notes below


# ─────────────────────────────────────────────────────────────────────────────
# Training corpus
# ─────────────────────────────────────────────────────────────────────────────
# This is the key knob. The classifier is only as good as these examples.
# Each entry is a (short) realistic snippet that a FIR (or non-FIR) would
# contain. We mix English, Urdu, and Roman-Urdu so the multilingual embedder
# learns the FIR concept across script and register.

_POSITIVE_EXAMPLES: List[str] = [
    # English FIRs
    "FIR Number 234/2024. Police Station Gulberg, District Lahore. Complainant Muhammad Usman, age 34. Incident: armed robbery at gunpoint. Section 392 PPC.",
    "First Information Report registered at Thana Saddar. Accused fled on motorcycle after shooting incident on 15-03-2024.",
    "Case No. 145/2023 under section 302 of the Pakistan Penal Code. Complainant Ahmed Khan reports murder of his brother. SHO Inspector Ali investigating.",
    "Complainant: Saima Bibi w/o Iqbal. CNIC 35201-1234567-9. Accused threatened her with a knife at her residence. Witnesses: two neighbours.",
    "FIR registered against unknown persons for theft of mobile phone. Place of incident: Liberty Market. District: Lahore. Date: 12/04/2024.",
    "Under section 324/34 PPC. Complainant injured by gunshot. Accused absconding. Recovered weapon: pistol with two empty shells.",
    # Urdu FIRs (genuine script)
    "ایف آئی آر نمبر 456/2024۔ تھانہ سدر، ضلع کراچی۔ مدعی احمد علی، عمر 42 سال۔ ڈکیتی کا واقعہ، دفعہ 392 مجموعہ تعزیرات پاکستان۔",
    "مقدمہ نمبر 78/2024 تحت دفعہ 302 مجموعہ تعزیرات۔ ملزم نامعلوم افراد نے گولی مار کر قتل کیا۔ گواہان موجود ہیں۔",
    "مدعی شکایت کنندہ نے بیان کیا کہ تین نامعلوم ملزمان موٹر سائیکل پر آئے اور اسلحہ کے زور پر لوٹ مار کی۔",
    "تھانہ گلبرگ میں ایف آئی آر درج۔ متاثرہ خاتون نے زیادتی کی شکایت درج کرائی۔ تفتیشی افسر انسپکٹر",
    # Roman-Urdu FIRs (common in Pakistani police reports)
    "FIR number 99 2024 thana saddar district karachi. Mulzim ne maddai ko goli mari. Section 302 PPC.",
    "Maddai ne bayan kiya ke unknown mulzim motorcycle par aaye aur loot maar ki. Gawah maujood hain.",
    "Mukadma darj kiya gaya hai under section 392 PPC. Mulzim arrest hua, pistol recover hui.",
    # Hybrid (matches real OCR'd documents)
    "FIR No. 67/2024 — تھانہ Gulberg — Complainant: Ali Hassan — ملزم: unknown persons — Section 380 PPC theft",
    "Case registered at police station. Maqam-e-waqia: Liberty Chowk. Date of incident: 22 March 2024. Accused fled the scene.",
]

_NEGATIVE_EXAMPLES: List[str] = [
    # Invoices / receipts
    "Invoice No. INV-001. Customer: Ahmed Trading Co. Items: 5x Widget at PKR 500 each. Subtotal 2500. GST 17%. Total 2925. Payment due in 30 days.",
    "Receipt: Cash payment received from Mr. Khan for consultancy services. Amount: 50,000 PKR. Thank you for your business.",
    # Academic / research
    "Abstract: This paper investigates the performance of transformer models on low-resource languages. Methodology section follows. Conclusion: results outperform the LSTM baseline.",
    "Chapter 3: Literature Review. The seminal work of Vaswani et al. (2017) introduced the attention mechanism that underpins modern NLP.",
    # News
    "BREAKING: Federal government announces new tax policy. Finance minister addresses parliament. Opposition criticises lack of consultation.",
    "Weather forecast for Lahore: high of 38 degrees Celsius with humidity at 60%. Light showers expected by evening.",
    # Business / finance
    "Q3 earnings report. Revenue increased 12% year-over-year to PKR 4.2 billion. Operating margin compressed slightly due to input costs.",
    # Personal / lifestyle
    "Recipe for biryani: marinate the chicken in yoghurt and spices for two hours. Cook rice separately until 70% done.",
    # Letters / general docs
    "Dear Sir, I am writing to apply for the position of software engineer advertised in your company. Please find my CV attached for your consideration.",
    "Memo: All staff are reminded that the office will be closed on Friday for Eid celebrations. Please complete pending tasks by Thursday evening.",
    # Urdu non-FIR
    "موسم کی پیش گوئی۔ لاہور میں آج زیادہ سے زیادہ درجہ حرارت 38 ڈگری سینٹی گریڈ ہے۔ بارش کا امکان شام تک ہے۔",
    "محترم جناب، میں آپ کی کمپنی میں سافٹ ویئر انجینئر کی پوسٹ کے لیے درخواست دینا چاہتا ہوں۔ شکریہ۔",
    "آج کے اخبار میں شائع ہوا کہ وفاقی حکومت نے نیا ٹیکس پلان متعارف کرایا ہے۔ اپوزیشن نے تنقید کی۔",
    # Garbage / OCR noise
    "asdf qwerty lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor incididunt ut labore et dolore",
    "Table 1 shows the experimental results. Figure 2 illustrates the architecture. See appendix A for hyperparameters.",
]


def _build_training_corpus() -> Tuple[List[str], List[int]]:
    """Exposed for `model_loader._fit_fir_classifier()`."""
    texts = _POSITIVE_EXAMPLES + _NEGATIVE_EXAMPLES
    labels = [1] * len(_POSITIVE_EXAMPLES) + [0] * len(_NEGATIVE_EXAMPLES)
    return texts, labels


# ─────────────────────────────────────────────────────────────────────────────
# Critical-field heuristic (sanity check on top of the classifier)
# ─────────────────────────────────────────────────────────────────────────────

_CRITICAL_PATTERNS: Dict[str, re.Pattern] = {
    "FIR Number":        re.compile(r"(fir|case|maqdam|mukadma|ایف آئی آر|مقدمہ)[\s\-#:نمبر]*\d+", re.I),
    "Complainant Name":  re.compile(r"(complainant|maddai|petitioner|مدعی|شاکی)[:\s]+\S+", re.I),
    "Date of Incident":  re.compile(
        r"\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4}|"
        r"(january|february|march|april|may|june|july|august|"
        r"september|october|november|december)\s+\d{1,2}", re.I),
    "Police Station":    re.compile(r"(police station|thana|تھانہ)[:\s]+\S+", re.I),
    "Section / Offence": re.compile(r"(section|dafaa|دفعہ|u/s|under\s+section)\s*\d+", re.I),
}


def check_missing_fields(text: str) -> List[str]:
    return [name for name, pat in _CRITICAL_PATTERNS.items() if not pat.search(text)]


# ─────────────────────────────────────────────────────────────────────────────
# XAI: keyword evidence (kept for the frontend XAI panel)
# ─────────────────────────────────────────────────────────────────────────────

# These aren't *used* by the classifier — they're a UI artifact. We surface
# any of these that appear in the input so the investigator sees a list of
# "signals the model noticed". The actual decision is made by SBERT + LR.
_DISPLAY_KEYWORDS_POSITIVE = [
    "FIR", "fir number", "police station", "complainant", "accused", "witness",
    "section", "PPC", "investigation", "arrest", "incident", "thana",
    "ایف آئی آر", "ملزم", "مدعی", "تھانہ", "دفعہ", "مقدمہ", "گواہ",
    "robbery", "murder", "theft", "assault", "kidnapping", "fraud",
]
_DISPLAY_KEYWORDS_NEGATIVE = [
    "invoice", "total due", "GST", "abstract", "methodology",
    "weather", "recipe", "quarterly", "earnings", "newsletter",
]


def _keyword_evidence(text: str) -> List[Dict]:
    lo = text.lower()
    ev: List[Dict] = []
    for kw in _DISPLAY_KEYWORDS_POSITIVE:
        if kw.lower() in lo:
            ev.append({"term": kw, "direction": "positive", "weight": 1.0})
    for kw in _DISPLAY_KEYWORDS_NEGATIVE:
        if kw.lower() in lo:
            ev.append({"term": kw, "direction": "negative", "weight": -1.0})
    return ev[:15]


# ─────────────────────────────────────────────────────────────────────────────
# Public result type
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    is_fir:           bool
    confidence:       float
    reason:           str
    evidence:         List[Dict] = field(default_factory=list)
    missing_critical: List[str]  = field(default_factory=list)
    method:           str        = "sbert+logreg"

    def to_dict(self) -> dict:
        return {
            "is_fir":           self.is_fir,
            "confidence":       round(self.confidence, 4),
            "reason":           self.reason,
            "evidence":         self.evidence,
            "missing_critical": self.missing_critical,
            "method":           self.method,
            # Back-compat keys for the orchestrator
            "keyword_hits":     [e["term"] for e in self.evidence if e["direction"] == "positive"],
            "top_features":     self.evidence,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Main validation function
# ─────────────────────────────────────────────────────────────────────────────

def validate_fir(text: str) -> dict:
    """Run the FIR classifier on `text` and return a dict ready for the
    orchestrator to merge into its response."""
    if not text or len(text.strip()) < 30:
        return ValidationResult(
            is_fir=False,
            confidence=0.0,
            reason="Text too short to analyse. Ensure the document is readable.",
            method="length_check",
        ).to_dict()

    embedder = models.sentence_embedder
    clf      = models.fir_classifier

    if embedder is None or clf is None:
        # Should never happen post-warmup, but degrade gracefully.
        logger.warning("Classifier unavailable — falling back to keyword heuristic.")
        return _heuristic_fallback(text)

    # ── Real prediction ──────────────────────────────────────────────────────
    emb = embedder.encode([text[:4000]], show_progress_bar=False)
    proba = clf.predict_proba(emb)[0]
    fir_prob = float(proba[1])

    evidence = _keyword_evidence(text)
    missing  = check_missing_fields(text)

    pos_terms = [e["term"] for e in evidence if e["direction"] == "positive"][:5]
    neg_terms = [e["term"] for e in evidence if e["direction"] == "negative"][:3]

    if fir_prob >= CONFIDENCE_THRESHOLD:
        reason = (
            f"Document classified as FIR ({fir_prob:.0%} confidence). "
            f"Detected indicators: {', '.join(pos_terms) if pos_terms else 'FIR-like structure'}."
        )
        if missing:
            reason += f" Heads-up: missing fields — {', '.join(missing)}."
        return ValidationResult(
            is_fir=True,
            confidence=fir_prob,
            reason=reason,
            evidence=evidence,
            missing_critical=missing,
        ).to_dict()

    reason = f"Document does not appear to be a FIR ({fir_prob:.0%} FIR confidence). "
    if neg_terms:
        reason += f"Non-FIR signals: {', '.join(neg_terms)}. "
    reason += "Please upload a valid First Information Report document."
    return ValidationResult(
        is_fir=False,
        confidence=fir_prob,
        reason=reason,
        evidence=evidence,
        missing_critical=[],
    ).to_dict()


def _heuristic_fallback(text: str) -> dict:
    """Tiny keyword-only validator used if the SBERT classifier failed to
    load. Not as accurate, but keeps the API from 500-ing in dev mode."""
    evidence = _keyword_evidence(text)
    pos = sum(1 for e in evidence if e["direction"] == "positive")
    neg = sum(1 for e in evidence if e["direction"] == "negative")
    is_fir = pos >= 3 and pos > neg
    confidence = min(0.9, 0.4 + 0.1 * pos - 0.15 * neg) if is_fir else max(0.1, 0.5 - 0.1 * neg)

    reason = (
        f"Heuristic validation (classifier unavailable). "
        f"Positive hits: {pos}, negative hits: {neg}."
    )
    return ValidationResult(
        is_fir=is_fir,
        confidence=confidence,
        reason=reason,
        evidence=evidence,
        missing_critical=check_missing_fields(text),
        method="heuristic_fallback",
    ).to_dict()