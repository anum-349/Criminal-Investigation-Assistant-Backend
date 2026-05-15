"""
pipeline/payload_generator.py

Converts extracted FIR entities → structured registration form payload.
The payload is designed to auto-fill frontend form fields.
"""

from typing import Optional, List, Any
import re


def _clean(value: Any) -> Any:
    """Normalize whitespace and strip artifacts from extracted values."""
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip().rstrip(".,;:/-")
    if isinstance(value, list):
        return [_clean(v) for v in value if v and str(v).strip()]
    return value


def generate_payload(fields: dict, extraction_meta: dict) -> dict:
    """
    Maps raw extracted fields to the frontend registration form payload.

    Args:
        fields:         Output of entity_extractor.extract_entities()["fields"]
        extraction_meta: {source_language, translation_coverage, ocr_method, ...}

    Returns:
        Structured dict ready for JSON response / form auto-fill
    """

    # ── Core FIR identifiers ─────────────────────────────────────────────────
    fir_info = {
        "firNumber":    _clean(fields.get("firNumber")),
        "caseTitle":    _clean(fields.get("caseTitle")),
        "policeStation": _clean(fields.get("policeStation")),
        "district":     _clean(fields.get("district")),
        "province":     _clean(fields.get("province")),
    }

    # ── Incident details ─────────────────────────────────────────────────────
    incident = {
        "dateOfIncident":  _clean(fields.get("dateOfIncident")),
        "timeOfIncident":  _clean(fields.get("timeOfIncident")),
        "incidentAddress": _clean(fields.get("incidentAddress")),
        "offenceType":     _clean(fields.get("offenceType")),
        "legalSections":   _clean(fields.get("legalSections", [])),
    }

    # ── Persons ──────────────────────────────────────────────────────────────
    persons = {
        "complainant": {
            "name":  _clean(fields.get("complainantName")),
            "cnic":  _clean(fields.get("complainantCNIC")),
            "age":   _clean(fields.get("complainantAge")),
            "phone": _clean(fields.get("complainantPhone")),
        },
        "accusedPersons": _clean(fields.get("accusedPersons", [])),
        "victims":        _clean(fields.get("victims", [])),
        "witnesses":      _clean(fields.get("witnesses", [])),
    }

    # ── Physical evidence ────────────────────────────────────────────────────
    evidence = {
        "weaponsInvolved":  list(set(_clean(fields.get("weaponsInvolved", [])))),
        "vehiclesInvolved": list(set(_clean(fields.get("vehiclesInvolved", [])))),
        "vehiclePlates":    _clean(fields.get("vehiclePlates", [])),
    }

    # ── Processing metadata ──────────────────────────────────────────────────
    meta = {
        "sourceLanguage":       extraction_meta.get("source_language", "unknown"),
        "translationCoverage":  extraction_meta.get("translation_coverage"),
        "unknownUrduTokens":    extraction_meta.get("unknown_tokens", []),
        "ocrMethod":            extraction_meta.get("ocr_method"),
        "ocrConfidence":        extraction_meta.get("ocr_confidence"),
        "completenessScore":    extraction_meta.get("completeness_score"),
        "missingCoreFields":    extraction_meta.get("missing_core_fields", []),
    }

    return {
        "status": "success",
        "payload": {
            "firInfo":  fir_info,
            "incident": incident,
            "persons":  persons,
            "evidence": evidence,
        },
        "meta": meta,
    }