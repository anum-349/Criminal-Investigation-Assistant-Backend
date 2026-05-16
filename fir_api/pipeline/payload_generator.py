"""
pipeline/payload_generator.py
─────────────────────────────
Maps the entity-extractor's flat field dict into the nested JSON envelope
that the React frontend's `StepFIRUpload` page consumes.

The orchestrator then *flattens* the envelope back into the form-field shape
the React `onExtracted()` callback wants. Two passes are simpler than they
sound — the nested envelope is also what other services (case export, audit
log) want, so we keep it as the canonical shape.
"""

from __future__ import annotations

import re
from typing import Any, List


def _clean(value: Any) -> Any:
    """Normalise whitespace and strip trailing punctuation from extracted values."""
    if value is None:
        return None
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip().rstrip(".,;:/-")
    if isinstance(value, list):
        cleaned = [_clean(v) for v in value if v is not None and str(v).strip()]
        # Deduplicate while preserving order
        return list(dict.fromkeys(cleaned))
    return value


def generate_payload(fields: dict, extraction_meta: dict) -> dict:
    """Returns:
        {
          "status": "success",
          "payload": {
              "firInfo":  {...},
              "incident": {...},
              "persons":  {...},
              "evidence": {...},
          },
          "meta": {...},
        }
    """
    fir_info = {
        "firNumber":     _clean(fields.get("firNumber")),
        "caseTitle":     _clean(fields.get("caseTitle")),
        "policeStation": _clean(fields.get("policeStation")),
        "district":      _clean(fields.get("district")),
        "province":      _clean(fields.get("province")),
    }

    incident = {
        "dateOfIncident":  _clean(fields.get("dateOfIncident")),
        "timeOfIncident":  _clean(fields.get("timeOfIncident")),
        "incidentAddress": _clean(fields.get("incidentAddress")),
        "offenceType":     _clean(fields.get("offenceType")),
        "legalSections":   _clean(fields.get("legalSections", [])),
    }

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

    evidence = {
        "weaponsInvolved":  _clean(fields.get("weaponsInvolved", [])),
        "vehiclesInvolved": _clean(fields.get("vehiclesInvolved", [])),
        "vehiclePlates":    _clean(fields.get("vehiclePlates", [])),
    }

    meta = {
        "sourceLanguage":      extraction_meta.get("source_language", "unknown"),
        "translationCoverage": extraction_meta.get("translation_coverage"),
        "unknownUrduTokens":   extraction_meta.get("unknown_tokens", []),
        "ocrMethod":           extraction_meta.get("ocr_method"),
        "ocrConfidence":       extraction_meta.get("ocr_confidence"),
        "completenessScore":   extraction_meta.get("completeness_score"),
        "missingCoreFields":   extraction_meta.get("missing_core_fields", []),
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