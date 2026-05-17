from __future__ import annotations

import re
from typing import Any, List


def _clean(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip().rstrip(".,;:/-")
    if isinstance(value, list):
        cleaned = [_clean(v) for v in value if v is not None and str(v).strip()]
        return list(dict.fromkeys(cleaned))
    return value


def generate_payload(fields: dict, extraction_meta: dict) -> dict:
    fir_info = {
        "firNumber":     _clean(fields.get("firNumber")),
        "caseTitle":     _clean(fields.get("caseTitle")),
        "policeStation": _clean(fields.get("policeStation")),
        "tehsil":        _clean(fields.get("tehsil")),
        "district":      _clean(fields.get("district")),
        "province":      _clean(fields.get("province")),
        "beat":          _clean(fields.get("beat")),
    }

    incident = {
        "dateOfReport":     _clean(fields.get("dateOfReport")),
        "dateOfIncident":   _clean(fields.get("dateOfIncident")),
        "timeOfIncident":   _clean(fields.get("timeOfIncident")),
        "incidentAddress":  _clean(fields.get("incidentAddress")),
        "distanceFromPS":   _clean(fields.get("distanceFromPS")),
        "offenceType":      _clean(fields.get("offenceType")),
        "allOffences":      _clean(fields.get("allOffences", [])),
        "legalSections":    _clean(fields.get("legalSections", [])),
        "applicableAct":    _clean(fields.get("applicableAct")),
        "sectionReadWith":  _clean(fields.get("sectionReadWith")),
    }

    persons = {
        "complainant": {
            "name":       _clean(fields.get("complainantName")),
            "father":     _clean(fields.get("complainantFather")),
            "cnic":       _clean(fields.get("complainantCNIC")),
            "age":        _clean(fields.get("complainantAge")),
            "phone":      _clean(fields.get("complainantPhone")),
            "caste":      _clean(fields.get("complainantCaste")),
            "profession": _clean(fields.get("complainantProfession")),
            "address":    _clean(fields.get("complainantAddress")),
        },
        "accusedPersons":  _clean(fields.get("accusedPersons", [])),
        "accusedUnknown":  bool(fields.get("accusedUnknown")),
        "victims":         _clean(fields.get("victims", [])),
        "witnesses":       _clean(fields.get("witnesses", [])),
    }

    evidence = {
        "weaponsInvolved":   _clean(fields.get("weaponsInvolved", [])),
        "vehiclesInvolved":  _clean(fields.get("vehiclesInvolved", [])),
        "vehiclePlates":     _clean(fields.get("vehiclePlates", [])),
        "hospitalReference": _clean(fields.get("hospitalReference")),
    }

    police = {
        "investigatingOfficer": _clean(fields.get("investigatingOfficer")),
        "ioRank":               _clean(fields.get("ioRank")),
        "beltNumber":           _clean(fields.get("beltNumber")),
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
            "police":   police,
        },
        "meta": meta,
    }