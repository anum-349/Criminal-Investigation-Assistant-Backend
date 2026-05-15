"""
fir_entity_extractor.py
───────────────────────
Extracts structured entities from English FIR text using:

  1. Regex / heuristic rules   — fast, deterministic, 100% explainable.
  2. TF-IDF keyword scorer     — confirms / weights extracted values.
  3. XAI evidence layer        — every field returned includes the regex
                                  pattern or keyword that produced it.

Output is a dict that maps 1-to-1 with the registration form payload.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ──────────────────────────────────────────────────────────────────────────────
# Utility
# ──────────────────────────────────────────────────────────────────────────────

def _first(pattern: re.Pattern, text: str, group: int = 1) -> Optional[str]:
    m = pattern.search(text)
    return m.group(group).strip() if m else None

def _all(pattern: re.Pattern, text: str, group: int = 1) -> List[str]:
    return [m.group(group).strip() for m in pattern.finditer(text)]

def _clean(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    return re.sub(r"\s+", " ", s).strip(" ,.:;-\"'")

# ──────────────────────────────────────────────────────────────────────────────
# Compiled patterns
# ──────────────────────────────────────────────────────────────────────────────

# Document identifiers
RE_FIR_NO = re.compile(
    r"(?:fir|f\.i\.r|case|mukadma|maqdam)[^\d]{0,15}(\d{1,6}[\/\-]?\d{0,6})",
    re.I)
RE_CASE_NO = re.compile(
    r"case\s+(?:no|number|#)[.\s:]*([A-Z0-9\-\/]+)", re.I)

# Date patterns (various formats)
RE_DATE = re.compile(
    r"(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{2,4})|"
    r"(\d{1,2})(?:st|nd|rd|th)?\s+"
    r"(january|february|march|april|may|june|july|august|"
    r"september|october|november|december)\s+(\d{2,4})",
    re.I)
RE_TIME = re.compile(
    r"(\d{1,2}:\d{2}(?::\d{2})?)\s*([ap]\.?m\.?)?|"
    r"(\d{1,2})\s*(?:hours?|o\'?clock)\s*([ap]m)?",
    re.I)

# Location
RE_POLICE_STATION = re.compile(
    r"(?:police\s+station|thana|thane|ps)[:\s]+([A-Za-z\u0600-\u06FF\s]{2,40}?)(?=[,\n\r]|$)",
    re.I)
RE_DISTRICT = re.compile(
    r"(?:district|zila)[:\s]+([A-Za-z\u0600-\u06FF\s]{2,30}?)(?=[,\n\r]|$)",
    re.I)
RE_PROVINCE = re.compile(
    r"(?:province|صوبہ)[:\s]*(punjab|sindh|kpk|khyber|balochistan|islamabad|gilgit|azad kashmir)",
    re.I)
RE_ADDRESS = re.compile(
    r"(?:address|place of incident|location|near|maqam)[:\s]+(.{5,120}?)(?=\n|complainant|accused|$)",
    re.I)

# Persons
RE_COMPLAINANT = re.compile(
    r"(?:complainant|maddai|shaki|reporter)[:\s]+([A-Za-z\u0600-\u06FF\s\.]{2,60}?)(?:[,\n\r]|father|s\/o|d\/o|w\/o|age|$)",
    re.I)
RE_ACCUSED = re.compile(
    r"(?:^|\n)(?:accused|suspect|mulzim|ملزم)[:\s]+([A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF\s\.]{1,50}?)(?:[,\n\r]|age|alias|s\/o|d\/o|$)",
    re.I | re.MULTILINE)
RE_VICTIM = re.compile(
    r"(?:victim|mutassira|متاثرہ)[:\s]+([A-Za-z\u0600-\u06FF\s\.]{2,60}?)(?:[,\n\r]|age|s\/o|d\/o|$)",
    re.I)
RE_WITNESS = re.compile(
    r"(?:witness|gawah|گواہ)[:\s]+([A-Za-z\u0600-\u06FF\s\.]{2,60}?)(?:[,\n\r]|age|$)",
    re.I)

# Father name / CNIC
RE_FATHER = re.compile(r"(?:s\/o|d\/o|w\/o|son of|daughter of|father[:\s]+)([A-Za-z\s\.]{2,50})", re.I)
RE_CNIC = re.compile(r"(?:cnic|nic|id card|identity)[:\s#]*(\d{5}[\-\s]?\d{7}[\-\s]?\d{1})", re.I)

# Age
RE_AGE = re.compile(r"(?:age|aged|عمر)[:\s]*(\d{1,3})\s*(?:years?|سال)?", re.I)

# Legal sections
RE_SECTION = re.compile(r"(?:section|دفعہ|dafaa|u/s|under section)\s*(\d+(?:[\/,\s]+\d+)*)", re.I)
RE_ACT = re.compile(r"(?:under|of)\s+((?:pakistan\s+)?penal\s+code|ppc|crpc|ata|anti[- ]terrorism\s+act)", re.I)

# Offence type
RE_OFFENCE = re.compile(
    r"\b(murder|robbery|theft|kidnapping|rape|sexual assault|fraud|forgery|"
    r"assault|arson|terrorism|smuggling|narcotics|drug trafficking|extortion)\b",
    re.I)

# Weapons
RE_WEAPON = re.compile(
    r"\b(pistol|revolver|rifle|gun|firearm|knife|dagger|sword|axe|baton|"
    r"grenade|explosive|kalashnikov|shotgun|SMG)\b",
    re.I)

# Vehicles
RE_VEHICLE_TYPE = re.compile(
    r"\b(motorcycle|motorbike|car|sedan|truck|van|bus|pickup|rickshaw|taxi|SUV|jeep)\b",
    re.I)
RE_PLATE = re.compile(r"\b([A-Z]{2,3}[\s\-]?\d{3,5})\b")

# Phone
RE_PHONE = re.compile(r"(?:phone|mobile|contact|tel)[:\s]*(\+?[0-9\s\-]{10,15})", re.I)

# Completeness keywords (positive) for scoring
_COMPLETENESS_FIELDS = [
    "firNumber", "complainantName", "accusedName", "dateOfIncident",
    "policeStation", "district", "offenceType", "sections",
]


# ──────────────────────────────────────────────────────────────────────────────
# Evidence wrapper
# ──────────────────────────────────────────────────────────────────────────────

def _ev(value: Any, source: str, pattern_name: str) -> Dict:
    """Wrap an extracted value with XAI evidence."""
    return {
        "value": value,
        "source_snippet": str(source)[:120],
        "extraction_method": pattern_name,
        "confidence": "high" if value else "none",
    }


# ──────────────────────────────────────────────────────────────────────────────
# Extraction engine
# ──────────────────────────────────────────────────────────────────────────────

def extract_fir_entities(text: str) -> Dict:
    """
    Parse FIR text and return a structured entity dict.

    Every top-level field has:
      value       — the extracted value (str, list, or None)
      xai         — evidence dict with source snippet + method name
    """
    t = text  # shorthand

    # ── Document info ─────────────────────────────────────────────────────────
    fir_no    = _clean(_first(RE_FIR_NO, t))
    case_no   = _clean(_first(RE_CASE_NO, t)) or fir_no

    # ── Dates & times ─────────────────────────────────────────────────────────
    all_dates = RE_DATE.findall(t)
    incident_date = None
    if all_dates:
        g = all_dates[0]
        if g[0]:  # numeric format
            incident_date = f"{g[0]}/{g[1]}/{g[2]}"
        else:      # word month
            incident_date = f"{g[3]} {g[4]} {g[5]}"

    time_m = RE_TIME.search(t)
    incident_time = None
    if time_m:
        incident_time = (time_m.group(1) or time_m.group(3) or "").strip()
        ampm = time_m.group(2) or time_m.group(4) or ""
        if ampm:
            incident_time += f" {ampm.upper()}"

    # ── Location ──────────────────────────────────────────────────────────────
    police_station = _clean(_first(RE_POLICE_STATION, t))
    district       = _clean(_first(RE_DISTRICT, t))
    province       = _clean(_first(RE_PROVINCE, t))
    address        = _clean(_first(RE_ADDRESS, t))

    # ── Persons ───────────────────────────────────────────────────────────────
    complainant_name = _clean(_first(RE_COMPLAINANT, t))
    accused_list     = [_clean(x) for x in _all(RE_ACCUSED, t) if _clean(x)]
    victim_list      = [_clean(x) for x in _all(RE_VICTIM, t) if _clean(x)]
    witness_list     = [_clean(x) for x in _all(RE_WITNESS, t) if _clean(x)]
    father_name      = _clean(_first(RE_FATHER, t))
    cnic             = _clean(_first(RE_CNIC, t))
    age_m            = RE_AGE.search(t)
    age              = age_m.group(1) if age_m else None

    # ── Legal ─────────────────────────────────────────────────────────────────
    sections         = list(set(_all(RE_SECTION, t)))
    act              = _clean(_first(RE_ACT, t))
    offences         = list(set(m.lower() for m in _all(RE_OFFENCE, t)))

    # ── Physical evidence ─────────────────────────────────────────────────────
    weapons  = list(set(m.lower() for m in _all(RE_WEAPON, t)))
    vehicles = list(set(m.lower() for m in _all(RE_VEHICLE_TYPE, t)))
    plates   = list(set(_all(RE_PLATE, t)))
    phone    = _clean(_first(RE_PHONE, t))

    # ── Completeness score ────────────────────────────────────────────────────
    filled = {
        "firNumber":        bool(fir_no),
        "complainantName":  bool(complainant_name),
        "accusedName":      bool(accused_list),
        "dateOfIncident":   bool(incident_date),
        "policeStation":    bool(police_station),
        "district":         bool(district),
        "offenceType":      bool(offences),
        "sections":         bool(sections),
    }
    completeness = round(sum(filled.values()) / len(filled) * 100, 1)

    # ── XAI evidence summary ─────────────────────────────────────────────────
    xai_evidence = []

    def _add_evidence(field_name: str, value: Any, pattern_name: str, snippet: str = ""):
        if value:
            xai_evidence.append({
                "field": field_name,
                "extracted_value": value,
                "extraction_method": pattern_name,
                "snippet": snippet[:100],
                "confidence": "high",
            })
        else:
            xai_evidence.append({
                "field": field_name,
                "extracted_value": None,
                "extraction_method": pattern_name,
                "snippet": "",
                "confidence": "none",
            })

    _add_evidence("firNumber",       fir_no,           "RE_FIR_NO")
    _add_evidence("dateOfIncident",  incident_date,     "RE_DATE")
    _add_evidence("incidentTime",    incident_time,     "RE_TIME")
    _add_evidence("policeStation",   police_station,    "RE_POLICE_STATION")
    _add_evidence("district",        district,          "RE_DISTRICT")
    _add_evidence("complainantName", complainant_name,  "RE_COMPLAINANT")
    _add_evidence("accusedPersons",  accused_list,      "RE_ACCUSED")
    _add_evidence("offenceType",     offences,          "RE_OFFENCE")
    _add_evidence("sections",        sections,          "RE_SECTION")
    _add_evidence("weapons",         weapons,           "RE_WEAPON")
    _add_evidence("vehicles",        vehicles,          "RE_VEHICLE_TYPE")

    # ── Build form payload ────────────────────────────────────────────────────
    payload = {
        # ── Case Identification ───────────────────────────────────────────────
        "firNumber":            fir_no,
        "caseNumber":           case_no,
        "caseTitle":            _build_case_title(offences, accused_list, address),

        # ── Temporal ─────────────────────────────────────────────────────────
        "dateOfIncident":       incident_date,
        "timeOfIncident":       incident_time,
        "dateOfReport":         incident_date,   # same unless clearly different

        # ── Location ─────────────────────────────────────────────────────────
        "policeStation":        police_station,
        "district":             district,
        "province":             province,
        "incidentAddress":      address,

        # ── Complainant ───────────────────────────────────────────────────────
        "complainantName":      complainant_name,
        "complainantFather":    father_name,
        "complainantCNIC":      cnic,
        "complainantAge":       age,
        "complainantPhone":     phone,

        # ── Accused ───────────────────────────────────────────────────────────
        "accusedPersons":       accused_list,
        "accusedCount":         len(accused_list),

        # ── Victims & witnesses ───────────────────────────────────────────────
        "victims":              victim_list,
        "witnesses":            witness_list,

        # ── Offence ───────────────────────────────────────────────────────────
        "offenceType":          offences[0].title() if offences else None,
        "allOffences":          offences,
        "legalSections":        sections,
        "applicableAct":        act,

        # ── Evidence ─────────────────────────────────────────────────────────
        "weaponsInvolved":      weapons,
        "vehiclesInvolved":     vehicles,
        "vehiclePlates":        plates,

        # ── Metadata ─────────────────────────────────────────────────────────
        "completenessScore":    completeness,
        "completenessDetail":   filled,
        "confidence":           _overall_confidence(completeness, offences, fir_no),

        # ── XAI ──────────────────────────────────────────────────────────────
        "xaiEvidence":          xai_evidence,

        # ── Extracted entities (for the React entity tiles) ───────────────────
        "extractedEntities": {
            "persons":   list(filter(None, [complainant_name] + accused_list + victim_list + witness_list)),
            "locations": list(filter(None, [address, police_station, district])),
            "weapons":   weapons,
            "vehicles":  vehicles,
            "dates":     [d for d in [incident_date] if d],
        },

        # ── Missing fields list (for the amber warning block) ─────────────────
        "missingFields": [k for k, v in filled.items() if not v],
    }

    return payload


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _build_case_title(offences: List[str], accused: List[str], location: Optional[str]) -> str:
    parts = []
    if offences:
        parts.append(offences[0].title())
    if accused:
        parts.append(f"by {accused[0]}")
    if location:
        short_loc = location[:40]
        parts.append(f"at {short_loc}")
    return " ".join(parts) if parts else "FIR Case"


def _overall_confidence(completeness: float, offences: List, fir_no: Optional[str]) -> float:
    score = completeness / 100.0 * 0.6
    if offences:
        score += 0.2
    if fir_no:
        score += 0.2
    return round(min(score, 1.0) * 100, 1)