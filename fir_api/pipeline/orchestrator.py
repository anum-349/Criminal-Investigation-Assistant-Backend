"""
pipeline/orchestrator.py
────────────────────────
End-to-end FIR processing pipeline.

Flow:
    1. Text extraction      (pdfplumber + Tesseract OCR with eng+urd)
    2. Language detection   (FastText)
    3. Urdu → English MT    (MarianMT)
    4. FIR validation       (multilingual SBERT + LogReg)
    5. Entity extraction    (regex + spaCy NER + mBERT Urdu NER)
    6. Payload generation   (frontend-shaped JSON)

Each step is small, isolated, and instrumented for XAI. The output preserves
the exact response shape the existing React frontend expects so the
fir-upload page works without changes.

Raises:
    ValueError — user-facing errors (non-FIR doc, unreadable scan). The
                 FastAPI handler translates these to HTTP 422 with a toast
                 message.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict

from pipeline.text_extractor   import extract_text
from pipeline.language_detector import detect_language
from pipeline.urdu_translator   import translate_urdu_to_english
from pipeline.fir_validator     import validate_fir
from pipeline.entity_extractor  import extract_entities
from pipeline.payload_generator import generate_payload
from pipeline.lime_explainer    import explain_with_lime
from pipeline.model_loader      import models

logger = logging.getLogger("fir.orchestrator")


def run_pipeline(file_path: str, original_filename: str = "") -> Dict[str, Any]:
    """Run the full pipeline. Returns the JSON-ready response dict.

    Response schema (matches the React fir-upload page):
        {
          "success": bool,
          "error":   Optional[str],
          "payload": {...entity fields for form auto-fill...},
          "steps":   {...XAI breakdown per stage...},
          "duration_ms": int,
        }
    """
    t0 = time.perf_counter()
    timings: Dict[str, float] = {}

    def _tick(name: str, start: float) -> None:
        timings[name] = round((time.perf_counter() - start) * 1000, 1)

    # ── 1. Extract raw text ──────────────────────────────────────────────────
    s = time.perf_counter()
    extraction = extract_text(file_path)
    raw_text = extraction.get("text", "") or ""
    _tick("extraction", s)

    if not raw_text or len(raw_text.strip()) < 20:
        raise ValueError(
            "Could not extract readable text. If the file is a scanned image, "
            "ensure the scan is clear and upright. If Tesseract OCR is missing, "
            "see the README installation steps."
        )

    # ── 2. Language detection ────────────────────────────────────────────────
    s = time.perf_counter()
    lang = detect_language(raw_text)
    _tick("language_detection", s)

    # ── 3. Translation (only if needed) ──────────────────────────────────────
    s = time.perf_counter()
    if lang.language in ("urdu", "mixed"):
        translation = translate_urdu_to_english(raw_text)
        english_text = translation["translated_text"]
        translation_skipped = False
    else:
        translation = {
            "translated_text":  raw_text,
            "source_language":  "english",
            "coverage":         1.0,
            "method":           "passthrough",
            "unknown_tokens":   [],
            "chunks_translated": 0,
        }
        english_text = raw_text
        translation_skipped = True
    _tick("translation", s)

    # ── 4. FIR validation ────────────────────────────────────────────────────
    # We validate on a *concatenation* of original + translated text — that
    # way we don't miss Urdu signals that Marian rewrote in a way the
    # classifier doesn't recognise, and English documents are unaffected.
    s = time.perf_counter()
    validation_input = raw_text if lang.language == "english" else f"{english_text}\n\n{raw_text}"
    validation = validate_fir(validation_input)
    _tick("validation", s)

    if not validation["is_fir"]:
        # User-facing rejection — bubble up so the API can toast it.
        raise ValueError(
            f"This document does not appear to be a FIR. {validation['reason']}"
        )

    # ── 5. LIME-style explanation (optional XAI) ─────────────────────────────
    s = time.perf_counter()
    try:
        # The new validator uses SBERT — we wrap it in a tiny adapter so LIME
        # can still perturb words and observe probability change.
        lime_result = _lime_on_sbert_classifier(english_text)
    except Exception as e:
        logger.warning("LIME explanation failed: %s", e)
        lime_result = {"lime_weights": [], "summary": "LIME unavailable for this request."}
    _tick("lime", s)

    # ── 6. Entity extraction ─────────────────────────────────────────────────
    s = time.perf_counter()
    entity_result = extract_entities(
        text=english_text,
        original_text=raw_text if lang.language in ("urdu", "mixed") else None,
    )
    _tick("entities", s)

    # ── 7. Payload generation ────────────────────────────────────────────────
    s = time.perf_counter()
    meta = {
        "source_language":      lang.language,
        "translation_coverage": translation["coverage"] if not translation_skipped else None,
        "unknown_tokens":       translation.get("unknown_tokens", []),
        "ocr_method":           extraction.get("method"),
        "ocr_confidence":       extraction.get("confidence"),
        "completeness_score":   entity_result["completeness_score"],
        "missing_core_fields":  entity_result["missing_core_fields"],
    }
    payload_envelope = generate_payload(entity_result["fields"], meta)
    _tick("payload", s)

    duration_ms = round((time.perf_counter() - t0) * 1000, 1)

    # ── Compose response in the shape the React component expects ───────────
    # The frontend uses both `result.payload.firNumber` (flat) and
    # `result.steps.validation.evidence` (nested). We support both by
    # flattening the payload here.
    flat_payload = _flatten_payload(payload_envelope, entity_result)

    return {
        "success":   True,
        "error":     None,
        "duration_ms": duration_ms,
        "payload":   flat_payload,
        "steps": {
            "extraction": {
                "method":     extraction.get("method"),
                "pages":      extraction.get("pages"),
                "char_count": len(raw_text),
                "has_urdu":   lang.script_ratio > 0.05,
                "confidence": extraction.get("confidence"),
            },
            "language_detection": lang.to_dict(),
            "translation": {
                "skipped":           translation_skipped,
                "method":            translation.get("method"),
                "coverage":          translation.get("coverage"),
                "chunks_translated": translation.get("chunks_translated", 0),
                "unknown_tokens":    translation.get("unknown_tokens", [])[:10],
            },
            "validation": validation,
            "lime":       lime_result,
            "entity_extraction": entity_result["xai_breakdown"],
        },
        "timings_ms": timings,
        "models_ready": models.status,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _flatten_payload(envelope: Dict, entity_result: Dict) -> Dict:
    """Flatten the nested payload + add the entity tiles the form expects."""
    p = envelope.get("payload", {})
    meta = envelope.get("meta", {})

    fir_info = p.get("firInfo", {})
    incident = p.get("incident", {})
    persons  = p.get("persons", {})
    evidence = p.get("evidence", {})

    return {
        # FIR identifiers
        "firNumber":          fir_info.get("firNumber"),
        "caseNumber":         fir_info.get("firNumber"),
        "caseTitle":          entity_result["fields"].get("caseTitle"),
        "policeStation":      fir_info.get("policeStation"),
        "district":           fir_info.get("district"),
        "province":           fir_info.get("province"),
        # Incident
        "dateOfIncident":     incident.get("dateOfIncident"),
        "timeOfIncident":     incident.get("timeOfIncident"),
        "incidentAddress":    incident.get("incidentAddress"),
        "offenceType":        incident.get("offenceType"),
        "allOffences":        entity_result["fields"].get("allOffences", []),
        "legalSections":      incident.get("legalSections", []),
        "applicableAct":      entity_result["fields"].get("applicableAct"),
        # Persons
        "complainantName":    persons.get("complainant", {}).get("name"),
        "complainantFather":  entity_result["fields"].get("complainantFather"),
        "complainantCNIC":    persons.get("complainant", {}).get("cnic"),
        "complainantAge":     persons.get("complainant", {}).get("age"),
        "complainantPhone":   persons.get("complainant", {}).get("phone"),
        "accusedPersons":     persons.get("accusedPersons", []),
        "victims":            persons.get("victims", []),
        "witnesses":          persons.get("witnesses", []),
        # Evidence
        "weaponsInvolved":    evidence.get("weaponsInvolved", []),
        "vehiclesInvolved":   evidence.get("vehiclesInvolved", []),
        "vehiclePlates":      evidence.get("vehiclePlates", []),
        # Frontend extras
        "completenessScore":  meta.get("completenessScore"),
        "confidence":         _overall_confidence(meta, entity_result),
        "extractedEntities":  entity_result.get("extractedEntities", {}),
        "missingFields":      entity_result.get("missingFields", []),
        "firLanguage":        meta.get("sourceLanguage", "english").capitalize(),
        "firType":            "FIR",
    }


def _overall_confidence(meta: Dict, entity_result: Dict) -> float:
    """Combine completeness + OCR confidence into a single 0-100 score for
    the toast message."""
    completeness = (meta.get("completenessScore") or 0) / 100.0
    ocr_conf     = meta.get("ocrConfidence") or 0.8
    return round(min(1.0, 0.5 * completeness + 0.5 * ocr_conf) * 100, 1)


# ─────────────────────────────────────────────────────────────────────────────
# LIME wrapper for the new SBERT classifier
# ─────────────────────────────────────────────────────────────────────────────

def _lime_on_sbert_classifier(text: str, num_samples: int = 80) -> Dict:
    """Lightweight LIME over the SBERT+LogReg pipeline.

    LIME is model-agnostic so we just need a `predict_proba(list[str]) -> ndarray`
    callable. We build one inline that reuses the warmed-up embedder.
    Sample count is lower than for TF-IDF (80 vs 200) because each forward
    pass through SBERT is ~10x more expensive than a TfidfVectorizer.transform,
    and 80 samples are enough for word-level attribution in practice.
    """
    if models.sentence_embedder is None or models.fir_classifier is None:
        return {"lime_weights": [], "summary": "LIME unavailable: classifier not loaded."}

    class _Wrapper:
        def predict_proba(self, texts):
            emb = models.sentence_embedder.encode(list(texts), show_progress_bar=False)
            return models.fir_classifier.predict_proba(emb)

    return explain_with_lime(text, _Wrapper(), num_samples=num_samples)