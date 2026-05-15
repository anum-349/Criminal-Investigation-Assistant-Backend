"""
pipeline/orchestrator.py

Runs the full FIR processing pipeline:
  1. Extract text (pdfplumber / OCR)
  2. Detect language; translate Urdu → English
  3. Validate: is this actually a FIR? (ML classifier + LIME)
  4. Extract named entities (regex + XAI tags)
  5. Generate form payload

Raises ValueError for user-facing errors (non-FIR doc, bad scan, etc.)
"""

from pipeline.text_extractor  import extract_text
from pipeline.urdu_translator  import translate_urdu_to_english
from pipeline.fir_validator    import validate_fir, _model as fir_model
from pipeline.entity_extractor import extract_entities
from pipeline.lime_explainer   import explain_with_lime
from pipeline.payload_generator import generate_payload


def run_pipeline(file_path: str, original_filename: str = "") -> dict:
    """
    Full pipeline.  Returns a dict suitable for JSON response.
    Raises ValueError for expected user-facing errors.
    """

    # ── Step 1: Extract raw text ─────────────────────────────────────────────
    extraction = extract_text(file_path)
    raw_text   = extraction["text"]

    if not raw_text or len(raw_text.strip()) < 20:
        raise ValueError(
            "Could not extract readable text from this file. "
            "If it's a scanned image, ensure the scan is clear and upright. "
            "If the Tesseract OCR engine is not installed, see the README."
        )

    # ── Step 2: Language detection + Urdu translation ────────────────────────
    translation   = translate_urdu_to_english(raw_text)
    english_text  = translation["translated_text"]
    source_lang   = translation["source_language"]
    coverage      = translation["coverage"]

    # ── Step 3: FIR validation ────────────────────────────────────────────────
    validation = validate_fir(english_text)

    if not validation["is_fir"]:
        raise ValueError(
            f"This document does not appear to be a FIR. "
            f"{validation['reason']} "
            "Please upload a First Information Report."
        )

    # ── Step 4: LIME explanation ──────────────────────────────────────────────
    lime_result = explain_with_lime(english_text, fir_model)

    # ── Step 5: Entity extraction ─────────────────────────────────────────────
    entity_result = extract_entities(english_text)

    # ── Step 6: Generate payload ──────────────────────────────────────────────
    meta = {
        "source_language":      source_lang,
        "translation_coverage": coverage if source_lang != "english" else None,
        "unknown_tokens":       translation.get("unknown_tokens", []),
        "ocr_method":           extraction["method"],
        "ocr_confidence":       extraction["confidence"],
        "completeness_score":   entity_result["completeness_score"],
        "missing_core_fields":  entity_result["missing_core_fields"],
    }

    payload_result = generate_payload(entity_result["fields"], meta)

    # ── Compose full response ─────────────────────────────────────────────────
    return {
        **payload_result,
        "xai": {
            "validation": {
                "is_fir":       validation["is_fir"],
                "confidence":   validation["confidence"],
                "keyword_hits": validation["keyword_hits"],
                "reason":       validation["reason"],
                "top_tfidf_features": validation["top_features"],
            },
            "lime": {
                "lime_weights": lime_result["lime_weights"],
                "summary":      lime_result["summary"],
            },
            "entity_extraction": entity_result["xai_breakdown"],
            "translation": {
                "source_language": source_lang,
                "coverage":        coverage,
                "unknown_tokens":  translation.get("unknown_tokens", []),
            },
            "ocr": {
                "method":     extraction["method"],
                "pages":      extraction["pages"],
                "confidence": extraction["confidence"],
            },
        },
    }