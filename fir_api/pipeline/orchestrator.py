from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Dict

from dotenv import load_dotenv
import fitz                         
import easyocr
from groq import Groq

from fir_api.pipeline.fir_validator     import validate_fir
from fir_api.pipeline.entity_extractor  import extract_entities
from fir_api.pipeline.payload_generator import generate_payload
from fir_api.pipeline.lime_explainer    import explain_with_lime
from fir_api.pipeline.model_loader      import models

logger = logging.getLogger("fir.orchestrator")

load_dotenv()
GROQ_FIR_API_KEY = os.getenv("GROQ_FIR_API_KEY", "")
GROQ_FIR_MODEL   = os.getenv("GROQ_FIR_MODEL",   "llama-3.3-70b-versatile")

_groq_client: Groq | None = None
_ocr_reader:  easyocr.Reader | None = None

CASE_TYPES = [
    "Murder / Homicide", "Rape / Sexual Assault", "Robbery / Armed Robbery",
    "Theft / Burglary", "Assault / Battery", "Fraud / Cybercrime",
    "Kidnapping / Abduction", "Drug Trafficking", "Extortion / Blackmail",
    "Missing Person", "Terrorism / Extremism", "Smuggling", "Other",
]

WEAPONS = [
    "Firearm (Pistol)", "Firearm (Rifle)", "Firearm (Shotgun)", "Knife / Blade",
    "Blunt Object", "Vehicle", "Chemical / Poison", "Explosive Device",
    "Hands / Feet (Physical)", "Unknown", "None / Not Applicable",
]

INJURY_TYPES = [
    "Fatal (Death)", "Severe / Critical", "Moderate", "Minor",
    "No Physical Injury", "Psychological Trauma",
]

VICTIM_STATUSES = ["Alive", "Deceased", "Critical Condition", "Hospitalized", "Missing"]

SUSPECT_STATUSES = [
    "At Large", "Detained", "Arrested", "Charged",
    "Absconding", "Under Surveillance", "Released",
]

THREAT_LEVELS = ["Low", "Medium", "High", "Critical"]

RELATION_TO_VICTIM = [
    "Stranger", "Neighbor", "Colleague", "Friend", "Family Member",
    "Spouse / Partner", "Ex-Partner", "Employer / Employee", "Unknown",
]

EVIDENCE_TYPES = [
    "CCTV / Video Footage", "Photograph", "Fingerprint", "DNA Sample",
    "Weapon", "Document / Paper", "Digital Evidence", "Witness Statement",
    "Medical Report", "Forensic Report", "Physical Object", "Other",
]

PROVINCES = [
    "Punjab", "Sindh", "Khyber Pakhtunkhwa", "Balochistan",
    "Islamabad Capital Territory", "Gilgit-Baltistan", "Azad Jammu & Kashmir",
]

CRIME_SCENE_TYPE = [
    "Residential – Indoor","Residential – Outdoor","Commercial – Indoor","Commercial – Outdoor","Public Space","Road / Highway","Industrial Area","Educational Institution","Religious Site","Hospital / Medical","Government Building","Other"
]

CITY_COORDS = {
    "karachi":     (24.8607, 67.0011),
    "lahore":      (31.5204, 74.3587),
    "islamabad":   (33.6844, 73.0479),
    "rawalpindi":  (33.5651, 73.0169),
    "faisalabad":  (31.4504, 73.1350),
    "peshawar":    (34.0151, 71.5249),
    "quetta":      (30.1798, 66.9750),
    "multan":      (30.1575, 71.5249),
    "hyderabad":   (25.3960, 68.3578),
    "gujranwala":  (32.1877, 74.1945),
    "sialkot":     (32.4945, 74.5229),
    "bahawalpur":  (29.3956, 71.6836),
}

def _ensure_coordinates(payload: dict) -> dict:
    """If LLM returned null coords, fall back to city center."""
    lat = payload.get("latitude")
    lon = payload.get("longitude")

    if lat and lon:
        return payload  

    city = (payload.get("city") or "").strip().lower()

    coords = CITY_COORDS.get(city)
    if not coords:
        for key, val in CITY_COORDS.items():
            if key in city or city in key:
                coords = val
                break

    if coords:
        payload["latitude"]  = coords[0]
        payload["longitude"] = coords[1]
    else:

        payload["latitude"]  = 30.3753
        payload["longitude"] = 69.3451

    return payload

def _get_groq() -> Groq:
    global _groq_client
    if _groq_client is None:
        if not GROQ_FIR_API_KEY:
            raise RuntimeError(
                "GROQ_FIR_API_KEY environment variable is not set. "
                "Add it to your .env or Docker environment."
            )
        _groq_client = Groq(api_key=GROQ_FIR_API_KEY)
    return _groq_client


def _get_ocr() -> easyocr.Reader:
    global _ocr_reader
    if _ocr_reader is None:
        logger.info("Loading EasyOCR (ur + en) …")
        _ocr_reader = easyocr.Reader(["ur", "en"], gpu=False, verbose=False)
        logger.info("✓ EasyOCR ready")
    return _ocr_reader

_URDU_TO_EN = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def _norm(text: str) -> str:
    return text.translate(_URDU_TO_EN)

_ARABIC_BLOCK = re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")

def _detect_language(text: str) -> str:
    non_ws = [c for c in text if not c.isspace()]
    if not non_ws:
        return "unknown"
    arabic_ratio = sum(1 for c in non_ws if _ARABIC_BLOCK.match(c)) / len(non_ws)
    if arabic_ratio > 0.40:
        return "urdu"
    if arabic_ratio > 0.10:
        return "mixed"
    return "english"

_VALID_PPC = {
    str(n) for n in
    list(range(109, 175)) + list(range(177, 220)) + list(range(267, 545)) +
    [147, 148, 149, 302, 324, 325, 326, 327, 328, 329, 330]
}

_VALID_PECA = {str(n) for n in range(3, 24)}
_VALID_SECTIONS = _VALID_PPC | _VALID_PECA

_MEMO_REF_PATTERN = re.compile(
    r"(?:no\.|incharge|ref|reference|fsd|ccrc|hq)[^0-9\n]*"
    r"(\d{2,4}(?:[\-\/]\d{2,4})+)",
    re.I,
)

def _filter_spurious_sections(sections: list[str], raw_text: str) -> list[str]:
    memo_numbers: set[str] = set()
    for m in _MEMO_REF_PATTERN.finditer(raw_text):
        for part in re.split(r"[\-\/]", m.group(1)):
            memo_numbers.add(part.strip())
    return [s for s in sections if s not in memo_numbers]


def _ocr_file(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".pdf":
        doc = fitz.open(file_path)
        direct_pages = []
        for page in doc:
            direct_pages.append(page.get_text("text"))
        doc.close()

        direct_text = "\n\n".join(direct_pages).strip()
        if len(direct_text) > 100:
            logger.info("Direct PDF text extraction succeeded (%d chars).", len(direct_text))
            return direct_text

        logger.info("Direct extraction yielded little text — using EasyOCR.")
        doc = fitz.open(file_path)
        pages: list[str] = []
        for i, page in enumerate(doc):
            pix  = page.get_pixmap(dpi=300)
            tmp  = f"_tmp_ocr_page_{i}.jpg"
            pix.save(tmp)
            pages.append(_ocr_image(tmp))
            try:
                os.remove(tmp)
            except OSError:
                pass
        doc.close()
        return "\n\n".join(pages)

    elif ext in {".jpg", ".jpeg", ".png", ".tiff", ".bmp"}:
        return _ocr_image(file_path)
    else:
        raise ValueError(f"Unsupported file extension: {ext}")


def _ocr_image(path: str) -> str:
    reader = _get_ocr()
    results = reader.readtext(path, detail=0, paragraph=True)
    return "\n".join(r for r in results if r.strip())

def _extract_regex(raw: str) -> dict:
    text = _norm(raw)
    found: dict = {}

    fir_candidates = re.findall(r"\b(\d{2,4})[/\\](\d{4})\b", text)
    if fir_candidates:
        for num, year in fir_candidates:
            if int(num) > 31 and 2000 <= int(year) <= 2030:
                found["fir_number"] = f"{num}/{year}"
                break
        if "fir_number" not in found:
            found["fir_number"] = f"{fir_candidates[-1][0]}/{fir_candidates[-1][1]}"

    date_hits = re.findall(r"\b(\d{2})[/-](\d{2})[/-](20\d{2})\b", text)
    if date_hits:
        d, m, y = date_hits[0]
        found["date_of_report"] = f"{y}-{m}-{d}"   # ISO for frontend date inputs
        if len(date_hits) > 1:
            d2, m2, y2 = date_hits[1]
            found["date_of_incident"] = f"{y2}-{m2}-{d2}"
    else:
        year_only = re.search(r"(?:year|Year)\s+(20\d{2})", text)
        if year_only:
            found["incident_year"] = year_only.group(1)

    time_hits = re.findall(r"\b(\d{1,2}:\d{2})\s*(?:PM|AM|بجے|pm|am)?\b", text)
    for t in time_hits:
        h, mi = t.split(":")
        if 0 <= int(h) <= 23 and 0 <= int(mi) <= 59:
            found["time"] = t
            break

    cnics = re.findall(r"\b(\d{5}-\d{7}-\d)\b", text)
    if not cnics:
        raw13 = re.findall(r"\b(\d{13})\b", text)
        cnics = [f"{c[:5]}-{c[5:12]}-{c[12]}" for c in raw13]
    if cnics:
        found["cnics"] = list(set(cnics))

    phones = re.findall(r"\b(0[3][0-9]{9})\b", re.sub(r"[\s\-]", "", text))
    if phones:
        found["contact_numbers"] = list(set(phones))

    nums     = re.findall(r"\b(\d{1,4})\b", text)
    sections = sorted(set(n for n in nums if n in _VALID_SECTIONS))
    sections = _filter_spurious_sections(sections, text)
    if sections:
        found["sections"] = sections

    ps_match = re.search(
        r"(?:police\s+station|p\.?s\.?|thana|تھانہ)\s*[:\-]?\s*"
        r"([A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF\s\.]{2,60}?)"
        r"(?=[,\n\r]|circle|district|$)",
        text, re.I,
    )
    if ps_match:
        found["police_station"] = ps_match.group(1).strip()

    circle_match = re.search(
        r"circle[/\s]*sub[\-\s]?circle\s*[:\-]?\s*"
        r"([A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF\s,\.]{2,60}?)"
        r"(?=[,\n\r]|$)",
        text, re.I,
    )
    if circle_match:
        found["circle"] = circle_match.group(1).strip()

    dist_match = re.search(
        r"(?:ضلع|district)\s*[:\-]?\s*([A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF\s]{1,25}?)"
        r"(?=[,\n\r]|tehsil|province|$)",
        text, re.I,
    )
    if dist_match:
        candidate = dist_match.group(1).strip()
        if not re.search(r"&|session|judge|court|additional", candidate, re.I):
            found["district"] = candidate

    if "district" not in found and "circle" in found:
        parts = [p.strip() for p in found["circle"].split(",")]
        if len(parts) > 1:
            found["district"] = parts[-1].title()

    return found

def _llama_extract(raw: str, regex: dict, language: str = "urdu") -> dict:
    logger.info("[Groq] Calling %s (lang=%s) …", GROQ_FIR_MODEL, language)

    if language == "english":
        system_msg = (
            "You are a Pakistani legal expert and document analyst. "
            "Extract information accurately from English-language FIR text. "
            "Return only valid JSON — no markdown, no explanation."
        )
    else:
        system_msg = (
            "You are a Pakistani legal expert. "
            "Extract information accurately from noisy OCR Urdu FIR text. "
            "Return only valid JSON — no markdown, no explanation."
        )

    if language == "english":
        doc_instructions = """You are an expert at reading Pakistani FIR documents written in English.
The text below may have minor OCR noise. Extract every field precisely.
Do not invent values — use null when a field is absent."""
    else:
        doc_instructions = """You are an expert at reading Pakistani FIR documents written in Urdu Nastaliq.
The text is OCR output and WILL have noise, merged words, and errors.
Interpret carefully using your knowledge of FIR structure and Pakistani names."""

    prompt = f"""{doc_instructions}

ALREADY CONFIRMED FIELDS (trust these, do not contradict):
{json.dumps(regex, ensure_ascii=False, indent=2)}

━━━ OPTION LISTS (your answers MUST match these exactly or be null) ━━━
case_type options      : {" | ".join(CASE_TYPES)}
weapon options         : {" | ".join(WEAPONS)}
injury_type options    : {" | ".join(INJURY_TYPES)}
victim_status options  : {" | ".join(VICTIM_STATUSES)}
suspect_status options : {" | ".join(SUSPECT_STATUSES)}
threat_level options   : {" | ".join(THREAT_LEVELS)}
relation options       : {" | ".join(RELATION_TO_VICTIM)}
evidence_type options  : {" | ".join(EVIDENCE_TYPES)}
province options       : {" | ".join(PROVINCES)}
crime_scene_type options: {" | ".join(CRIME_SCENE_TYPE)}

━━━ KEY EXTRACTION RULES ━━━
- Dates → ISO format YYYY-MM-DD
- Times → HH:MM (24-hour)
- Sections → extract ALL section numbers from "U/S X, Y r/w Z PPC/PECA 2016"
- Do NOT mistake memo/reference numbers (e.g. FSD/351-357) for legal sections
- Phone → Pakistani mobile 03xx format (11 digits). Reject IMEI numbers.
- CNIC → 5-7-1 format e.g. 35202-1234567-1
- Province → must exactly match one of the province options or null
- weapon_used → must exactly match one of the weapon options or null
- case_type → must exactly match one of the case_type options or null
- For each accused: extract their own vehicle separately if mentioned
- "ولد" = son of (Urdu), "S/O" = son of, "D/O" = daughter of, "W/O" = wife of

FIR TEXT:
{raw[:6000]}

━━━ RETURN ONLY THIS JSON ━━━
{{
  "fir_number":             "NNN/YYYY or null",
  "case_title":             "short descriptive title e.g. 'Murder of Shahzad at Gulberg'",
  "case_type":              "exact match from case_type options or null",
  "priority":               "High | Medium | Low or null",
  "ppc_sections":           "comma-separated section string e.g. '302, 324, 34' or null",
  "description":            "2-3 sentence English summary of the crime",

  "incident_date":          "YYYY-MM-DD or null",
  "incident_time":          "HH:MM or null",
  "reporting_date":         "YYYY-MM-DD or null",
  "reporting_time":         "HH:MM or null",

  "reporting_officer":      "name of officer who registered the FIR or null",
  "assigned_investigator":  "investigating officer full name or null",
  "io_rank":                "rank e.g. Sub Inspector, ASI or null",

  "province":               "exact match from province options or null",
  "city":                   "city / district name or null",
  "area":                   "area / locality / mohalla or null",
  "police_station":         "police station name or null",
  "address":                "full incident address/location or null",
  "latitude":  "latitude as decimal number — REQUIRED. If exact location unknown, use city center coordinates. Never null.",
  "longitude": "longitude as decimal number — REQUIRED. If exact location unknown, use city center coordinates. Never null.",
  "crime_scene_type":       "exact match from crime_scene_type options or null",
  "scene_access":           "Open (Unsecured)","Secured by Police","Forensic Team Present","Released","Restricted Access" or null,
  "landmarks":              "nearest landmark or cross-street or null",

  "weapon_used":            "exact match from weapon options or null",
  "weapon_description":     "additional detail about the weapon e.g. '9mm pistol, unlicensed' or null",
  "vehicle_used":           "vehicle description if used in crime or null",
  "num_suspects":           "integer count of suspects or null",
  "motive":                 "stated or inferred motive or null",
  "modus":                  "how the crime was carried out or null",
  "witness_available":      true or false,
  "cctv":                   true or false,
  "crime_description":      "detailed narrative of the crime in English",

  "cause_of_death":         "cause of death if murder/homicide or null",
  "body_location":          "where body was found or null",
  "time_of_death":          "HH:MM or null",
  "postmortem_done":        true or false,
  "forensic_done":          true or false,

  "medical_exam":           "medical examination findings if rape case or null",
  "victim_counseling":      true or false,
  "protection_order":       true or false,

  "stolen_items":           "description of stolen items or null",
  "stolen_value":           "estimated value in PKR as number or null",
  "recovery_status":        "Recovered | Partial | Not Recovered or null",
  "entry_point":            "how accused entered premises or null",

  "victims": [
    {{
      "name":             "full name",
      "age":              "integer or null",
      "gender":           "Male | Female | Other or null",
      "cnic":             "CNIC 5-7-1 or null",
      "contact":          "03xx phone or null",
      "address":          "address or null",
      "occupation":       "job/profession or null",
      "status":           "exact match from victim_status options",
      "injury_type":      "exact match from injury_type options or null",
      "nature_of_injuries": "describe injuries in detail or null",
      "cause_of_death":   "if fatal, how they died or null",
      "declared_dead":    "At scene | On arrival at hospital | During treatment | Pronounced later | Unknown or null",
      "postmortem_autopsy": "Conducted | Conducted, Pending Report | Scheduled | Pending | Not Required | Refused by Family or null",
      "injury_summary":   "one-line summary of injuries or null",
      "relation":         "exact match from relation options — relation of accused to this victim",
      "threat_level":     "exact match from threat_level options or null",
      "medical_report":   true or false,
      "protection_required": true or false,
      "statement":        "victim statement if recorded in FIR or null"
    }}
  ],

  "suspects": [
    {{
      "name":                 "full name or 'Unknown'",
      "age":                  "integer or null",
      "gender":               "Male | Female | Other or null",
      "cnic":                 "CNIC 5-7-1 or null",
      "address":              "address or null",
      "contact":              "03xx phone or null",
      "occupation":           "job/profession or null",
      "physical_description": "height, build, complexion, clothing at time of offence or null",
      "known_affiliations":   "gang, group, or known criminal links or null",
      "criminal_record":      true or false,
      "arrested":             true or false,
      "status":               "exact match from suspect_status options",
      "vehicle_description":  "suspect vehicle details or null",
      "arrival_method":       "how suspect arrived/fled e.g. 'on motorcycle' or null",
      "relation_to_case":     "exact match from relation options — relation to victim or null",
      "alibi":                "alibi if mentioned or null",
      "notes":                "any other relevant notes or null",
      "reason":               "basis for suspicion or null"
    }}
  ],

  "witnesses": [
    {{
      "name":                   "full name",
      "age":                    "integer or null",
      "gender":                 "Male | Female | Other or null",
      "contact":                "03xx phone or null",
      "address":                "address or null",
      "witness_type":           "Eyewitness | Earwitness | Character Witness | Expert Witness | Victim-Witness | Hearsay Witness | Other",
      "relation_to_case":       "exact match from relation options",
      "statement_date":         "YYYY-MM-DD or null",
      "statement_recorded_by":  "officer name who recorded statement or null",
      "description":            "what the witness saw or heard",
      "anonymous":              false,
      "protection_required":    false,
      "cooperating":            true
    }}
  ],

  "evidences": [
    {{
      "type":           "exact match from evidence_type options",
      "description":    "description of the evidence item",
      "date_collected": "YYYY-MM-DD or null",
      "collected_by":   "officer name or null"
    }}
  ],

  "crime_summary":      "2-3 sentence English summary",
  "statement_english":  "Complete formal English translation/transcription of the entire FIR narrative."
}}"""

    resp = _get_groq().chat.completions.create(
        model=GROQ_FIR_MODEL,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user",   "content": prompt},
        ],
        temperature=0.05,
        max_tokens=4096,
    )

    raw_resp = resp.choices[0].message.content.strip()
    if "```" in raw_resp:
        parts    = raw_resp.split("```")
        raw_resp = parts[1] if len(parts) > 1 else raw_resp
        if raw_resp.startswith("json"):
            raw_resp = raw_resp[4:]
    raw_resp = raw_resp.strip()

    try:
        return json.loads(raw_resp)
    except json.JSONDecodeError:
        logger.warning("Groq JSON parse failed — returning partial")
        return {"statement_english": raw_resp}

def _s(v):
    """Return stripped string or None."""
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _bool(v, default=False):
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.lower() in ("true", "yes", "1")
    return default


def _int_or_none(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _merge_to_form(regex: dict, llama: dict) -> dict:
    """
    Map Groq extraction output directly to emptyFormData field names
    so the frontend can consume the payload without any re-mapping.
    Every key here corresponds to a field in emptyFormData / buildCreateCasePayload.
    """

    groq_sections = llama.get("legal_sections") or llama.get("ppc_sections") or []
    if isinstance(groq_sections, str):
        sections_str = groq_sections
    else:
        sections_str = ", ".join(str(s) for s in groq_sections if s)
    if not sections_str:
        regex_sections = regex.get("sections") or []
        sections_str = ", ".join(regex_sections)

    victims_out = []
    for v in (llama.get("victims") or []):
        if not v.get("name"):
            continue
        victims_out.append({
            "name":              _s(v.get("name")),
            "age":               _s(v.get("age")),
            "gender":            _s(v.get("gender")),
            "cnic":              _s(v.get("cnic")),
            "contact":           _s(v.get("contact")),
            "address":           _s(v.get("address")),
            "occupation":        _s(v.get("occupation")),
            "status":            _s(v.get("status")) or "Alive",
            "injuryType":        _s(v.get("injury_type")),
            "natureOfInjuries":  _s(v.get("nature_of_injuries")),
            "causeOfDeath":      _s(v.get("cause_of_death")),
            "declaredDead":      _s(v.get("declared_dead")),
            "postmortemAutopsy": _s(v.get("postmortem_autopsy")),
            "injurySummary":     _s(v.get("injury_summary")),
            "relation":          _s(v.get("relation")),
            "threatLevel":       _s(v.get("threat_level")),
            "medicalReport":     _bool(v.get("medical_report")),
            "protectionRequired":_bool(v.get("protection_required")),
            "cooperative":       _bool(v.get("cooperating"), True),
            "statement":         _s(v.get("statement")),
            "forensic":          [],
            "timeline":          [],
            "legal":             [],
            "nextFollowUp":      "",
            "protectionAssigned":"",
            "protectionNotes":   "",
            "primaryLabel":      "",
            "caseType":          _s(llama.get("case_type")),
            "injuryRecordedBy":  "",
            "postmortem":        False,
        })

    suspects_out = []
    for s in (llama.get("suspects") or []):
        if not s.get("name"):
            continue
        suspects_out.append({
            "name":                _s(s.get("name")),
            "age":                 _s(s.get("age")),
            "gender":              _s(s.get("gender")),
            "cnic":                _s(s.get("cnic")),
            "address":             _s(s.get("address")),
            "contact":             _s(s.get("contact")),
            "occupation":          _s(s.get("occupation")),
            "physicalDescription": _s(s.get("physical_description")),
            "knownAffiliations":   _s(s.get("known_affiliations")),
            "criminalRecord":      _bool(s.get("criminal_record")),
            "arrested":            _bool(s.get("arrested")),
            "status":              _s(s.get("status")) or "At Large",
            "vehicleDescription":  _s(s.get("vehicle_description")),
            "arrivalMethod":       _s(s.get("arrival_method")),
            "relationToCase":      _s(s.get("relation_to_case")),
            "alibi":               _s(s.get("alibi")),
            "notes":               _s(s.get("notes")),
            "reason":              _s(s.get("reason")),
        })

    witnesses_out = []
    for w in (llama.get("witnesses") or []):
        if not w.get("name"):
            continue
        witnesses_out.append({
            "name":                 _s(w.get("name")),
            "age":                  _s(w.get("age")),
            "gender":               _s(w.get("gender")),
            "contact":              _s(w.get("contact")),
            "address":              _s(w.get("address")),
            "witnessType":          _s(w.get("witness_type")),
            "relationToCase":       _s(w.get("relation_to_case")),
            "credibility":          "Medium — Unverified",   # default; not in FIR
            "status":               "Active",
            "statementDate":        _s(w.get("statement_date")),
            "statementRecordedBy":  _s(w.get("statement_recorded_by")),
            "description":          _s(w.get("description")),
            "anonymous":            _bool(w.get("anonymous")),
            "protectionRequired":   _bool(w.get("protection_required")),
            "cooperating":          _bool(w.get("cooperating"), True),
        })

    evidences_out = []
    for e in (llama.get("evidences") or []):
        if not e.get("type") and not e.get("description"):
            continue
        evidences_out.append({
            "type":          _s(e.get("type")),
            "description":   _s(e.get("description")),
            "file":          None,
            "fileName":      "",
            "dateCollected": _s(e.get("date_collected")),
            "collectedBy":   _s(e.get("collected_by")),
        })

    groq_sections_list = llama.get("legal_sections") or []
    if isinstance(groq_sections_list, str):
        groq_sections_list = [x.strip() for x in groq_sections_list.split(",") if x.strip()]
    if not groq_sections_list:
        groq_sections_list = regex.get("sections") or []

    province_raw = _s(llama.get("province"))
    province = province_raw if province_raw in PROVINCES else None

    return {
        "firFile":             None,
        "firFileName":         "",
        "firLanguage":         _s(llama.get("source_language", "")).title() or "English",
        "firType":             "FIR",
        "manualEntry":         False,

        "firNumber":           _s(llama.get("fir_number")) or regex.get("fir_number"),
        "caseTitle":           _s(llama.get("case_title")),
        "incidentDate":        _s(llama.get("incident_date")) or regex.get("date_of_incident"),
        "incidentTime":        _s(llama.get("incident_time")) or regex.get("time"),
        "reportingDate":       _s(llama.get("reporting_date")) or regex.get("date_of_report"),
        "reportingTime":       _s(llama.get("reporting_time")),
        "caseType":            _s(llama.get("case_type")),
        "priority":            _s(llama.get("priority")),
        "caseStatus":          "Open",
        "ppcSections":         sections_str or None,
        "description":         _s(llama.get("description")) or _s(llama.get("crime_summary")),
        "reportingOfficer":    _s(llama.get("reporting_officer")),
        "assignedInvestigator":_s(llama.get("assigned_investigator")),

        "province":            province,
        "city":                _s(llama.get("city")) or regex.get("district"),
        "area":                _s(llama.get("area")),
        "policeStation":       _s(llama.get("police_station")) or regex.get("police_station"),
        "address":             _s(llama.get("address")),
        "latitude":            llama.get("latitude"),
        "longitude":           llama.get("longitude"),
        "crimeSceneType":      _s(llama.get("crime_scene_type")),
        "sceneAccess":         _s(llama.get("scene_access")),
        "landmarks":           _s(llama.get("landmarks")),

        "victims":             victims_out or [],

        "suspects":            suspects_out or [],

        "weaponUsed":          _s(llama.get("weapon_used")),
        "weaponDescription":   _s(llama.get("weapon_description")),
        "vehicleUsed":         _s(llama.get("vehicle_used")),
        "numSuspects":         _int_or_none(llama.get("num_suspects")),
        "motive":              _s(llama.get("motive")),
        "modus":               _s(llama.get("modus")),
        "witnessAvailable":    _bool(llama.get("witness_available")),
        "cctv":                _bool(llama.get("cctv")),
        "crimeDescription":    _s(llama.get("crime_description")),

        "causeOfDeath":        _s(llama.get("cause_of_death")),
        "bodyLocation":        _s(llama.get("body_location")),
        "timeOfDeath":         _s(llama.get("time_of_death")),
        "postmortemDone":      _bool(llama.get("postmortem_done")),
        "forensicDone":        _bool(llama.get("forensic_done")),

        "medicalExam":         _s(llama.get("medical_exam")),
        "victimCounseling":    _bool(llama.get("victim_counseling")),
        "protectionOrder":     _bool(llama.get("protection_order")),

        "stolenItems":         _s(llama.get("stolen_items")),
        "stolenValue":         llama.get("stolen_value"),
        "recoveryStatus":      _s(llama.get("recovery_status")),
        "entryPoint":          _s(llama.get("entry_point")),

        "evidences":           evidences_out or [],

        "witnesses":           witnesses_out or [],

        "extractedEntities":   None,   # filled later by entity_extractor
        "completeness":        0,
        "confidence":          0,
        "missingFields":       [],

        "statementEnglish":    _s(llama.get("statement_english")),
        "crimeSummary":        _s(llama.get("crime_summary")),

        "_legalSections":      groq_sections_list,
        "_ioRank":             _s(llama.get("io_rank")),
    }

def _patch_form_from_entities(form: dict, entity_fields: dict) -> None:
    """
    After entity_extractor runs, backfill any form fields that are still
    empty with whatever NER found.  We never overwrite a value that Groq
    already set — Groq output is higher quality.
    """
    BACKFILL = {
        "policeStation":       "policeStation",
        "district":            "city",
        "province":            "province",
        "complainantName":     None,           
        "investigatingOfficer":"assignedInvestigator",
        "offenceType":         "caseType",
        "incidentAddress":     "address",
    }
    for ent_key, form_key in BACKFILL.items():
        if not form_key:
            continue
        if form.get(form_key):
            continue   
        val = entity_fields.get(ent_key)
        if val:
            form[form_key] = val

def run_pipeline(file_path: str, original_filename: str = "", on_step: callable = None) -> Dict[str, Any]:
    t0      = time.perf_counter()
    timings: Dict[str, float] = {}

    def _tick(name: str, start: float) -> None:
        timings[name] = round((time.perf_counter() - start) * 1000, 1)

    def _emit(step: str, label: str):
        if on_step:
            on_step({"step": step, "label": label})

    s = time.perf_counter()
    _emit("ocr", "OCR / text extraction…")
    raw_text = _ocr_file(file_path)
    _tick("ocr", s)

    if not raw_text or len(raw_text.strip()) < 20:
        raise ValueError(
            "Could not extract readable text from the document. "
            "Ensure the scan is clear and upright."
        )

    language = _detect_language(raw_text)
    _emit("lang", f"Detected language: {language}")
    logger.info("[LANG] Detected language: %s", language)

    s = time.perf_counter()
    _emit("regex", "Running regex extraction…")
    regex_fields = _extract_regex(raw_text)
    logger.info("[REGEX] %s", json.dumps(regex_fields, ensure_ascii=False))
    _tick("regex", s)

    s = time.perf_counter()
    _emit("groq", "Extracting with AI…")
    llama_fields = _llama_extract(raw_text, regex_fields, language=language)
    llama_fields["source_language"] = language
    llama_fields = _ensure_coordinates(llama_fields)
    _tick("groq", s)

    s = time.perf_counter()
    english_text = llama_fields.get("statement_english", "") or raw_text
    combined_for_validation = f"{english_text}\n\n{raw_text}"
    validation = validate_fir(combined_for_validation)
    _tick("validation", s)

    if not validation["is_fir"]:
        raise ValueError(
            f"This document does not appear to be a FIR. {validation['reason']}"
        )

    s = time.perf_counter()
    _emit("merge", "Mapping to form fields…")
    form_data = _merge_to_form(regex_fields, llama_fields)
    _tick("merge", s)

    s = time.perf_counter()
    _emit("entities", "Running NER backfill…")
    ner_input = raw_text if language == "english" else english_text
    entity_result = extract_entities(text=ner_input, original_text=raw_text)
    _patch_form_from_entities(form_data, entity_result["fields"])
    _tick("entities", s)

    s = time.perf_counter()
    try:
        lime_result = _lime_on_sbert_classifier(english_text)
    except Exception as e:
        logger.warning("LIME explanation failed: %s", e)
        lime_result = {"lime_weights": [], "summary": "LIME unavailable."}
    _tick("lime", s)

    s = time.perf_counter()
    meta = {
        "source_language":      language,
        "ocr_method":           "pymupdf_direct" if language == "english" else "easyocr",
        "completeness_score":   entity_result["completeness_score"],
        "missing_core_fields":  entity_result["missing_core_fields"],
    }
    payload_envelope = generate_payload(entity_result["fields"], meta)
    _tick("payload", s)

    form_data["completeness"]      = entity_result["completeness_score"]
    form_data["confidence"]        = _overall_confidence(meta)
    form_data["missingFields"]     = entity_result["missing_core_fields"]
    form_data["extractedEntities"] = entity_result.get("extractedEntities", {})

    duration_ms = round((time.perf_counter() - t0) * 1000, 1)

    return {
        "success":     True,
        "error":       None,
        "duration_ms": duration_ms,

        "payload": form_data,

        "steps": {
            "ocr": {
                "method":     meta["ocr_method"],
                "char_count": len(raw_text),
                "language":   language,
            },
            "regex":  regex_fields,
            "groq": {
                "model":            GROQ_FIR_MODEL,
                "suspects_count":   len(form_data["suspects"]),
                "victims_count":    len(form_data["victims"]),
                "witnesses_count":  len(form_data["witnesses"]),
                "evidences_count":  len(form_data["evidences"]),
                "statement_length": len(english_text),
            },
            "validation":        validation,
            "lime":              lime_result,
            "entity_extraction": entity_result["xai_breakdown"],
        },
        "timings_ms":   timings,
        "models_ready": models.status,
    }

def _overall_confidence(meta: dict) -> float:
    completeness = (meta.get("completeness_score") or 0) / 100.0
    return round(completeness * 100, 1)


def _lime_on_sbert_classifier(text: str, num_samples: int = 80) -> dict:
    if models.sentence_embedder is None or models.fir_classifier is None:
        return {"lime_weights": [], "summary": "LIME unavailable: classifier not loaded."}

    class _Wrapper:
        def predict_proba(self, texts):
            emb = models.sentence_embedder.encode(list(texts), show_progress_bar=False)
            return models.fir_classifier.predict_proba(emb)

    return explain_with_lime(text, _Wrapper(), num_samples=num_samples)