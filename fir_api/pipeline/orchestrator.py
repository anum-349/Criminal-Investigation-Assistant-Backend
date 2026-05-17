from __future__ import annotations

import logging
import time
from typing import Any, Dict

from pipeline.text_extractor    import extract_text
from pipeline.language_detector import detect_language
from pipeline.urdu_translator   import translate_urdu_to_english
from pipeline.fir_validator     import validate_fir
from pipeline.entity_extractor  import extract_entities
from pipeline.payload_generator import generate_payload
from pipeline.lime_explainer    import explain_with_lime
from pipeline.model_loader      import models

logger = logging.getLogger("fir.orchestrator")


def run_pipeline(file_path: str, original_filename: str = "") -> Dict[str, Any]:
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
    elif lang.language == "unknown":
        translation = {
            "translated_text":  raw_text,
            "source_language":  "unknown",
            "coverage":         0.0,
            "method":           "passthrough_unknown",
            "unknown_tokens":   [],
            "chunks_translated": 0,
        }
        english_text = raw_text
        translation_skipped = True
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
    s = time.perf_counter()
    validation_input = raw_text if lang.language == "english" else f"{english_text}\n\n{raw_text}"
    validation = validate_fir(validation_input)
    _tick("validation", s)

    if not validation["is_fir"]:
        raise ValueError(
            f"This document does not appear to be a FIR. {validation['reason']}"
        )

    # ── 5. LIME explanation (XAI) ────────────────────────────────────────────
    s = time.perf_counter()
    try:
        lime_result = _lime_on_sbert_classifier(english_text)
    except Exception as e:
        logger.warning("LIME explanation failed: %s", e)
        lime_result = {"lime_weights": [], "summary": "LIME unavailable for this request."}
    _tick("lime", s)

    # ── 6. Entity extraction (Pakistani FIR tuned) ──────────────────────────
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

    flat_payload = _flatten_payload(payload_envelope, entity_result)

    return {
        "success":     True,
        "error":       None,
        "duration_ms": duration_ms,
        "payload":     flat_payload,
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
            "validation":        validation,
            "lime":              lime_result,
            "entity_extraction": entity_result["xai_breakdown"],
        },
        "timings_ms":   timings,
        "models_ready": models.status,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Flatten nested envelope → flat shape the React form's onExtracted expects
# ─────────────────────────────────────────────────────────────────────────────

def _flatten_payload(envelope: Dict, entity_result: Dict) -> Dict:
    p = envelope.get("payload", {})
    meta = envelope.get("meta", {})

    fir_info = p.get("firInfo", {})
    incident = p.get("incident", {})
    persons  = p.get("persons", {})
    evidence = p.get("evidence", {})
    police   = p.get("police", {})

    complainant = persons.get("complainant", {})

    return {
        # ── FIR identifiers ──────────────────────────────────────────────────
        "firNumber":          fir_info.get("firNumber"),
        "caseNumber":         fir_info.get("firNumber"),
        "caseTitle":          fir_info.get("caseTitle"),

        # ── Location hierarchy (Pakistan-specific) ──────────────────────────
        "policeStation":      fir_info.get("policeStation"),
        "tehsil":             fir_info.get("tehsil"),
        "district":           fir_info.get("district"),
        "province":           fir_info.get("province"),
        "beat":               fir_info.get("beat"),

        # ── Incident ─────────────────────────────────────────────────────────
        "dateOfReport":       incident.get("dateOfReport"),
        "dateOfIncident":     incident.get("dateOfIncident"),
        "timeOfIncident":     incident.get("timeOfIncident"),
        "incidentAddress":    incident.get("incidentAddress"),
        "distanceFromPS":     incident.get("distanceFromPS"),
        "offenceType":        incident.get("offenceType"),
        "allOffences":        incident.get("allOffences", []),
        "legalSections":      incident.get("legalSections", []),
        "applicableAct":      incident.get("applicableAct"),
        "sectionReadWith":    incident.get("sectionReadWith"),

        # ── Complainant (full Pakistani identity block) ─────────────────────
        "complainantName":      complainant.get("name"),
        "complainantFather":    complainant.get("father"),
        "complainantCNIC":      complainant.get("cnic"),
        "complainantAge":       complainant.get("age"),
        "complainantPhone":     complainant.get("phone"),
        "complainantCaste":     complainant.get("caste"),
        "complainantProfession": complainant.get("profession"),
        "complainantAddress":   complainant.get("address"),

        # ── Other persons ───────────────────────────────────────────────────
        "accusedPersons":     persons.get("accusedPersons", []),
        "accusedUnknown":     persons.get("accusedUnknown", False),
        "victims":            persons.get("victims", []),
        "witnesses":          persons.get("witnesses", []),

        # ── Evidence ────────────────────────────────────────────────────────
        "weaponsInvolved":    evidence.get("weaponsInvolved", []),
        "vehiclesInvolved":   evidence.get("vehiclesInvolved", []),
        "vehiclePlates":      evidence.get("vehiclePlates", []),
        "hospitalReference":  evidence.get("hospitalReference"),

        # ── Police / IO ─────────────────────────────────────────────────────
        "investigatingOfficer": police.get("investigatingOfficer"),
        "ioRank":               police.get("ioRank"),
        "beltNumber":           police.get("beltNumber"),

        # ── Frontend extras ─────────────────────────────────────────────────
        "completenessScore":  meta.get("completenessScore"),
        "confidence":         _overall_confidence(meta),
        "extractedEntities":  entity_result.get("extractedEntities", {}),
        "missingFields":      entity_result.get("missingFields", []),
        "firLanguage":        (meta.get("sourceLanguage") or "english").capitalize(),
        "firType":            "FIR",
    }


def _overall_confidence(meta: Dict) -> float:
    completeness = (meta.get("completenessScore") or 0) / 100.0
    ocr_conf     = meta.get("ocrConfidence") or 0.8
    return round(min(1.0, 0.5 * completeness + 0.5 * ocr_conf) * 100, 1)


def _lime_on_sbert_classifier(text: str, num_samples: int = 80) -> Dict:
    if models.sentence_embedder is None or models.fir_classifier is None:
        return {"lime_weights": [], "summary": "LIME unavailable: classifier not loaded."}

    class _Wrapper:
        def predict_proba(self, texts):
            emb = models.sentence_embedder.encode(list(texts), show_progress_bar=False)
            return models.fir_classifier.predict_proba(emb)

    return explain_with_lime(text, _Wrapper(), num_samples=num_samples)