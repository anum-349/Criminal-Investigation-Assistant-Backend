from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from fir_api.pipeline.model_loader import models

logger = logging.getLogger("fir.entities")


# ═════════════════════════════════════════════════════════════════════════════
# Regex patterns — Pakistani FIR format
# ═════════════════════════════════════════════════════════════════════════════

RE_FIR_NO = re.compile(
    r"(?:"
    r"fir(?:\s+no\.?|\s+number|\s*#)?|"
    r"ایف\s*آئی\s*آر(?:\s*نمبر)?|"
    r"مقدمہ(?:\s*نمبر)?|"
    r"case\s+(?:no\.?|number)|"
    r"maqdam(?:\s+number)?|"
    r"mukadma(?:\s+number)?"
    r")\s*[:#\-]?\s*"
    r"([A-Z]{0,4}[\-\s]?\d{1,5}\s*[\/\-]\s*\d{2,4})",
    re.I,
)

RE_FIR_NO_BARE = re.compile(
    r"(?:^|\n|\s)(\d{2,5}\s*/\s*\d{2,4})(?:\s|$|\n)",
)

RE_DATE = re.compile(
    r"\b(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})\b|"
    r"\b((?:january|february|march|april|may|june|july|august|"
    r"september|october|november|december)\s+\d{1,2}(?:,?\s+\d{4})?)\b|"
    r"\b(\d{1,2}\s+(?:january|february|march|april|may|june|july|august|"
    r"september|october|november|december)(?:,?\s+\d{4})?)\b|"
    r"([\u06F0-\u06F9]{1,2}[\-\/][\u06F0-\u06F9]{1,2}[\-\/][\u06F0-\u06F9]{2,4})",
    re.I,
)

RE_TIME = re.compile(
    r"\b(\d{1,2}\s*[:.]\s*\d{2}\s*(?:am|pm|hrs?|hours|بجے)?)\b|"
    r"\b(\d{4}\s*(?:hrs?|hours))\b",
    re.I,
)

RE_POLICE_STATION = re.compile(
    r"(?:"
    r"police\s+station|p\.?s\.?|"
    r"thana|"
    r"ٹھانہ|تھانہ|قائمہ"
    r")\s*[:\-]?\s*"
    r"([A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF\s\.]{2,60}?)"
    r"(?=[,\n\r]|circle|district|ضلع|sec|دفعہ|$)",
    re.I,
)

RE_DISTRICT = re.compile(
    r"(?:district|zila|ضلع)\s*[:\-]?\s*"
    r"([A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF\s]{2,30}?)"
    r"(?=[,\n\r]|province|صوبہ|tehsil|تحصیل|$)",
    re.I,
)

RE_TEHSIL = re.compile(
    r"(?:tehsil|تحصیل)\s*[:\-]?\s*"
    r"([A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF\s]{2,30}?)"
    r"(?=[,\n\r]|district|ضلع|$)",
    re.I,
)

RE_PROVINCE = re.compile(
    r"(?:province|suba|صوبہ)\s*[:\-]?\s*("
    r"punjab|sindh|balochistan|kpk|khyber\s+pakhtunkhwa|"
    r"پنجاب|سندھ|بلوچستان|خیبر\s*پختونخوا|"
    r"islamabad|آزاد\s*کشمیر|gilgit|گلگت)",
    re.I,
)

RE_CNIC = re.compile(r"\b(\d{5}\s*[\-]?\s*\d{7}\s*[\-]?\s*\d)\b")

# FIX: RE_PHONE — must match a Pakistani mobile/landline format only.
# Old pattern matched bare digit sequences including IMEI numbers.
# Now requires the number to start with 0 (Pakistani) or +92, with 10-11 digits.
RE_PHONE = re.compile(
    r"(?:phone|mobile|contact|tel|cell|موبائل|فون|رابطہ)\s*[:\-]?\s*"
    r"(\+?92[-\s]?\d{3}[-\s]?\d{7}|"
    r"0\d{3}[-\s]?\d{7}|"
    r"0\d{2}[-\s]?\d{7,8})",
    re.I,
)

# FIX: RE_PHONE_BARE — explicit 03xx start (11 digits), so IMEI (14+ digits) won't match.
RE_PHONE_BARE = re.compile(
    r"\b(03\d{2}[\-\s]?\d{3}[\-\s]?\d{4})\b"
    r"|\b(\+92[\-\s]?3\d{2}[\-\s]?\d{3}[\-\s]?\d{4})\b"
)

RE_AGE = re.compile(r"\b(?:age|aged|عمر)\s*[:\-]?\s*(\d{1,3})\s*(?:years?|سال)?", re.I)

# FIX: RE_FATHER — the old pattern fired on "s/o" appearing inside words like
# "software", extracting garbage like "ftware".  Added a word-boundary assertion
# before the s/o abbreviation and tightened the separator requirement.
RE_FATHER = re.compile(
    r"(?<![a-z])"            # negative lookbehind: not preceded by a-z (catches "soft**ware**")
    r"\b(?:"
    r"s/o|d/o|w/o|"
    r"son\s+of|daughter\s+of|wife\s+of|"
    r"ولد(?:یت)?|"
    r"بنت|"
    r"زوجہ|"
    r"اہلیہ"
    r")\b"
    r"\s*[:\-]?\s*"
    r"([A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF\s\.]{1,50}?)"
    r"(?=[,\n\r]|age|cnic|عمر|شناختی|موبائل|سکنہ|caste|$)",
    re.I,
)

RE_CASTE = re.compile(
    r"(?:caste|قوم|ذات)\s*[:\-]?\s*"
    r"([A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF\s]{2,30}?)"
    r"(?=[,\n\r]|profession|پیشہ|سکنہ|cnic|شناختی|$)",
    re.I,
)

RE_PROFESSION = re.compile(
    r"(?:profession|occupation|پیشہ|پیشے|peshay?)\s*[:\-]?\s*"
    r"([A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF\s]{2,40}?)"
    r"(?=[,\n\r]|سکنہ|resident|cnic|شناختی|$)",
    re.I,
)

RE_RESIDENCE = re.compile(
    r"(?:"
    r"resident\s+of|address|"
    r"سکنہ|سکونت|پتہ|مکین|"
    r"محلہ|گاؤں|بستی"
    r")\s*[:\-]?\s*"
    r"([A-Za-z\u0600-\u06FF\d][A-Za-z\u0600-\u06FF\d\s,\.\-/]{3,120}?)"
    r"(?=[,\n\r]{2,}|caste|قوم|cnic|شناختی|موبائل|phone|$)",
    re.I,
)

RE_INCIDENT_PLACE = re.compile(
    r"(?:"
    r"place\s+of\s+(?:incident|occurrence)|"
    r"incident\s+location|"
    r"maqam[-\s]?(?:e[-\s]?)?waqia|"
    r"مقام\s*وقوعہ|"
    r"جائے\s*وقوعہ"
    r")\s*[:\-]?\s*"
    r"(.{5,200}?)(?=\n\n|\n[0-9]|complainant|accused|مدعی|ملزم|تفصیل|$)",
    re.I | re.DOTALL,
)

RE_DISTANCE_FROM_PS = re.compile(
    r"(?:بمسافت|پر|distance\s+of|approximately)\s*"
    r"(\d+(?:\.\d+)?)\s*"
    r"(کلومیٹر|km|kilometers?|میٹر|m|metre|miles?|میل)"
    r"\s*(شمال|جنوب|مشرق|مغرب|north|south|east|west|northeast|northwest|southeast|southwest)?",
    re.I,
)

RE_BEAT = re.compile(
    r"(?:beat|بیلٹ|پولیس\s*چوکی|chowki|چوکی)\s*[:\-]?\s*"
    r"([A-Za-z\u0600-\u06FF\d][A-Za-z\u0600-\u06FF\d\s\.\-/]{1,30}?)"
    r"(?=[,\n\r]|$)",
    re.I,
)

RE_COMPLAINANT_LABEL = re.compile(
    r"(?:"
    r"complainant|petitioner|informant|reporter|"
    r"اطلاع\s*دہندہ|مدعی|شاکی|مستغیث|"
    r"maddai|shaki|mustaghees"
    r")\s*[:\-]?\s*"
    r"([A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF\s\.]{1,60}?)"
    r"(?=[,\n\r]|s/o|d/o|w/o|ولد|بنت|زوجہ|cnic|age|عمر|$)",
    re.I,
)

RE_ACCUSED_LABEL = re.compile(
    r"(?:^|\n|\.|،)\s*"
    r"(?:accused(?:\s+person(?:s)?)?|suspect|culprit|"
    r"مدعا\s*علیہ|ملزم(?:ان|ین)?|مشتبہ|مجرم|"
    r"mulzim|mulzimaan)"
    r"\s*[:\-]?\s*"
    r"([A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF\s\.،]{1,80}?)"
    r"(?=[,\n\r]|age|alias|s/o|d/o|cnic|عمر|شناختی|$)",
    re.I | re.MULTILINE,
)

RE_ACCUSED_UNKNOWN = re.compile(
    r"(?:accused|mulzim|ملزم(?:ان|ین)?)\s*[:\-]?\s*"
    r"(unknown|نامعلوم|na[\s\-]?maloom)"
    r"\s*(?:persons?|individuals?|افراد)?",
    re.I,
)

RE_VICTIM_LABEL = re.compile(
    r"(?:"
    r"victim|injured|deceased|"
    r"متاثرہ?|متاثرین|مقتول|زخمی|"
    r"mutassir|mutassira"
    r")\s*[:\-]?\s*"
    r"([A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF\s\.]{1,60}?)"
    r"(?=[,\n\r]|age|s/o|d/o|عمر|ولد|$)",
    re.I,
)

RE_WITNESS_LABEL = re.compile(
    r"(?:"
    r"witness(?:es)?|eyewitness|"
    r"گواہ(?:ان)?|چشم\s*دید|"
    r"gawah|gawahan"
    r")\s*[:\-]?\s*"
    r"([A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF\s\.]{1,60}?)"
    r"(?=[,\n\r]|age|s/o|d/o|عمر|ولد|$)",
    re.I,
)

RE_SECTION = re.compile(
    r"(?:"
    r"section|"
    r"u/s|under\s+section|"
    r"دفعہ|دفعات|"
    r"dafaa|dafa"
    r")\s*[:\-]?\s*"
    r"(\d+(?:[\s,/]+\d+)*)",
    re.I,
)

RE_SECTION_LIST_ITEM = re.compile(
    r"(?:^|\n)\s*(\d{2,4})\s*(?:ت\s*پ|ppc|crpc|peca|ata)?",
    re.I | re.MULTILINE,
)

RE_ACT = re.compile(
    r"\b("
    r"(?:pakistan\s+)?penal\s+code|ppc|p\.p\.c\.?|"
    r"تعزیرات\s*پاکستان|ت\s*پ|"
    r"crpc|cr\.p\.c\.?|ضابطہ\s*فوجداری|"
    r"(?:anti[\-\s]?)?terrorism\s+act|ata|انسداد\s*دہشت\s*گردی|"
    r"control\s+of\s+narcotics?\s+substances?\s+act|cnsa|"
    r"hudood\s+ordinance|حدود|"
    r"(?:prevention\s+of\s+)?electronic\s+crimes\s+act|peca|peda|"
    r"peca\s*20\d{2}"
    r")\b",
    re.I,
)

RE_SECTION_RW = re.compile(
    r"(\d+(?:[\s,/]+\d+)*)\s*(?:r/?w|read\s+with|متعلقہ)\s*(\d+(?:[\s,/]+\d+)*)",
    re.I,
)

RE_OFFENCE = re.compile(
    r"\b("
    r"murder|qatal|قتل|homicide|"
    r"attempt(?:ed)?\s+(?:to\s+)?murder|qatal[\-\s]e[\-\s]amad|اقدام\s*قتل|"
    r"robbery|dakaiti|ڈکیتی|loot(?:ing|ed)?|لوٹ\s*مار|"
    r"theft|chori|چوری|burglary|sariqa|نقب\s*زنی|"
    r"kidnap(?:ping)?|abduction|اغواء?|اغوا|"
    r"rape|sexual\s+assault|zina[\-\s]bil[\-\s]jabr|زنا\s*بالجبر|"
    r"assault|battery|مار\s*پیٹ|تشدد|"
    r"fraud|forgery|cheating|دھوکہ\s*دہی|جعل\s*سازی|"
    r"arson|آتش\s*زنی|"
    r"terrorism|extremism|دہشت\s*گردی|"
    r"smuggling|narcotics?|drug\s+trafficking|منشیات|"
    r"extortion|bhatta|بھتہ|"
    r"hurt|injury|قتل\s*و\s*غارت|"
    r"firing|aerial\s+firing|فائرنگ|"
    r"trespass|illegal\s+entry|غیر\s*قانونی\s*مداخلت|"
    r"family\s+dispute|خاندانی\s*جھگڑا|"
    r"land\s+dispute|زمین\s*کا\s*تنازعہ|"
    r"cyber\s+crime|cyber\s+fraud|"
    r"unauthorized\s+access|unauthorized\s+transmission|"
    r"unfair\s+means|cheating\s+in\s+examination|electronic\s+crime"
    r")\b",
    re.I,
)

RE_WEAPON = re.compile(
    r"\b("
    r"pistol|revolver|rifle|gun|firearm|بندوق|پستول|"
    r"kalashnikov|ak[\-\s]?47|smg|shotgun|کلاشن\s*کوف|"
    r"30[\-\s]?bore|9mm|7\.62|"
    r"knife|dagger|chaqu|چاقو|خنجر|sword|تلوار|axe|کلہاڑی|"
    r"grenade|explosive|bomb|ied|دستی\s*بم|بم|"
    r"baton|stick|lathi|لاٹھی|iron\s+rod|سریا|stone|پتھر|"
    r"acid|تیزاب|"
    r"اسلحہ|اسلحہ\s*ناجائز"
    r")\b",
    re.I,
)

RE_VEHICLE = re.compile(
    r"\b(motor[\s\-]?cycle|motor[\s\-]?bike|bike|"
    r"موٹر\s*سائیکل|موٹرسائیکل|"
    r"car|sedan|suv|jeep|گاڑی|کار|"
    r"suzuki|honda|toyota|corolla|cultus|mehran|swift|spacia|"
    r"truck|van|bus|pickup|trolley|"
    r"rickshaw|auto|qingqi|چنگچی|رکشہ|"
    r"taxi|cab|"
    r"tractor|ٹریکٹر)\b",
    re.I,
)

# FIX: RE_PLATE — the old pattern stopped at 2 alpha chars after hyphen, so
# "LEE-17-1316" was cut to "LEE-17".  Extended to capture full Pakistani plate
# format including multiple hyphen groups.
RE_PLATE = re.compile(
    r"\b([A-Z]{2,4}[\s\-]\d{2,5}(?:[\s\-][A-Z0-9]{1,6})*)\b"
)

RE_IO_RANK = re.compile(
    r"\b("
    r"IGP|AIGP|DIG|SSP|SP|DSP|ASP|"
    r"inspector|insp|sub[\-\s]?inspector|si|"
    r"assistant\s+sub[\-\s]?inspector|asi|"
    r"assistant\s+director|additional\s+director|deputy\s+director|"
    r"head\s+constable|hc|"
    r"constable|"
    r"sho|"
    r"انسپکٹر|سب\s*انسپکٹر|اسسٹنٹ\s*سب\s*انسپکٹر|ہیڈ\s*کانسٹیبل|کانسٹیبل|افسر|تفتیشی\s*افسر"
    r")\b\.?\s*"
    r"([A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF\s\.]{2,40}?)?"
    r"(?=[,\n\r]|signature|دستخط|belt|$)",
    re.I,
)

# FIX: RE_IO_NAME — the old pattern matched "i.o." inside common words like
# "information" (the substring "ion" → no, but "transmission of information"
# triggered because "i" + ".o." was found).  The real issue is the pattern
# has no word-boundary on the right of the abbreviation.  Also added
# "entrusted to" phrasing common in English FIRs.
RE_IO_NAME = re.compile(
    r"(?:"
    r"(?:investigation\s+is\s+)?entrusted\s+to|"
    r"\bi\.?o\.?\b|"
    r"investigating\s+officer|"
    r"تفتیشی\s*افسر|"
    r"انچارج"
    r")"
    r"\s*[:\-]?\s*"
    r"([A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF\s\.]{2,50}?)"
    r"(?=[,\n\r]|signature|دستخط|assistant|$)",
    re.I,
)

RE_BELT_NUMBER = re.compile(
    r"(?:belt|بیلٹ\s*نمبر|بیلٹ)\s*(?:no\.?|number|نمبر)?\s*[:\-]?\s*"
    r"(\d{2,5}\s*[/]?\s*[A-Z]?)",
    re.I,
)

RE_HOSPITAL = re.compile(
    r"\b(THQ|DHQ|RHC|BHU|"
    r"tehsil\s+headquarters?\s+hospital|"
    r"district\s+headquarters?\s+hospital|"
    r"hospital|اسپتال|ہسپتال)\s*"
    r"(\d{2,6})?",
    re.I,
)


# ═════════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════════

def _first(pat: re.Pattern, text: str) -> Optional[str]:
    m = pat.search(text)
    if not m:
        return None
    for g in m.groups():
        if g and g.strip():
            return g.strip()
    return None


def _all(pat: re.Pattern, text: str) -> List[str]:
    results = []
    for m in pat.finditer(text):
        for g in m.groups():
            if g and g.strip():
                results.append(g.strip())
                break
    return results


def _snippet(text: str, value: str, context: int = 60) -> str:
    if not value or not text:
        return ""
    idx = text.find(value)
    if idx == -1:
        return ""
    start = max(0, idx - context)
    end   = min(len(text), idx + len(value) + context)
    return text[start:end].replace("\n", " ")


def _evidence(field: str, value: Any, method: str, snippet: str) -> Dict:
    has_value = bool(value) if not isinstance(value, list) else len(value) > 0
    return {
        "field":            field,
        "extracted_value":  value,
        "extraction_method": method,
        "snippet":          snippet,
        "confidence":       "high" if has_value else "none",
    }


# ═════════════════════════════════════════════════════════════════════════════
# spaCy NER helper
# ═════════════════════════════════════════════════════════════════════════════

def _spacy_ents(text: str) -> Dict[str, List[str]]:
    nlp = models.spacy_nlp
    if nlp is None:
        return {"PERSON": [], "ORG": [], "GPE": [], "LOC": [], "DATE": []}
    doc = nlp(text[:10000])
    result: Dict[str, List[str]] = {"PERSON": [], "ORG": [], "GPE": [], "LOC": [], "DATE": []}
    for ent in doc.ents:
        if ent.label_ in result:
            result[ent.label_].append(ent.text.strip())
    return result


# ═════════════════════════════════════════════════════════════════════════════
# Main extractor
# ═════════════════════════════════════════════════════════════════════════════

def extract_entities(text: str, original_text: str = "") -> Dict[str, Any]:
    """Extract all structured fields from `text` (English translation or raw).

    `original_text` is the raw OCR output — regex patterns that target Urdu
    script run against it while spaCy / English patterns run against `text`.
    """
    if not text or len(text.strip()) < 10:
        return _empty_result()

    t    = text        # English text (for English patterns + spaCy)
    orig = original_text or text   # Urdu/mixed OCR (for Urdu-script patterns)
    xai: List[Dict] = []

    # ── FIR Number ────────────────────────────────────────────────────────────
    fir_no = _first(RE_FIR_NO, t) or _first(RE_FIR_NO, orig)
    if not fir_no:
        m = RE_FIR_NO_BARE.search(t) or RE_FIR_NO_BARE.search(orig)
        fir_no = m.group(1).replace(" ", "") if m else None

    xai.append(_evidence("firNumber", fir_no, "regex:RE_FIR_NO", _snippet(t, fir_no or "")))

    # ── Dates ─────────────────────────────────────────────────────────────────
    all_dates = _all(RE_DATE, t)
    date_of_report   = all_dates[0] if len(all_dates) >= 1 else None
    date_of_incident = all_dates[1] if len(all_dates) >= 2 else date_of_report

    xai.append(_evidence("dateOfReport",   date_of_report,   "regex:RE_DATE[0]", ""))
    xai.append(_evidence("dateOfIncident", date_of_incident, "regex:RE_DATE[1]", ""))

    # ── Time ──────────────────────────────────────────────────────────────────
    time_of_incident = _first(RE_TIME, t)
    xai.append(_evidence("timeOfIncident", time_of_incident, "regex:RE_TIME[0]", ""))

    # ── Location fields ───────────────────────────────────────────────────────
    police_station = _first(RE_POLICE_STATION, t) or _first(RE_POLICE_STATION, orig)
    district       = _first(RE_DISTRICT, t)       or _first(RE_DISTRICT, orig)
    tehsil         = _first(RE_TEHSIL,   t)       or _first(RE_TEHSIL,   orig)
    province       = _first(RE_PROVINCE, t)       or _first(RE_PROVINCE, orig)
    incident_place = _first(RE_INCIDENT_PLACE, t) or _first(RE_INCIDENT_PLACE, orig)
    beat           = _first(RE_BEAT, t)           or _first(RE_BEAT, orig)

    dist_m      = RE_DISTANCE_FROM_PS.search(t) or RE_DISTANCE_FROM_PS.search(orig)
    distance_from_ps = None
    if dist_m:
        val  = dist_m.group(1)
        unit = dist_m.group(2)
        dirn = dist_m.group(3) or ""
        distance_from_ps = f"{val} {unit} {dirn}".strip()

    xai.extend([
        _evidence("policeStation", police_station, "regex:RE_POLICE_STATION", _snippet(t, police_station or "")),
        _evidence("district",      district,       "regex:RE_DISTRICT",       _snippet(t, district or "")),
        _evidence("tehsil",        tehsil,         "regex:RE_TEHSIL",         _snippet(t, tehsil or "")),
        _evidence("province",      province,       "regex:RE_PROVINCE",       _snippet(t, province or "")),
        _evidence("incidentPlace", incident_place, "regex:RE_INCIDENT_PLACE", _snippet(t, incident_place or "")),
        _evidence("beat",          beat,           "regex:RE_BEAT",           _snippet(t, beat or "")),
        _evidence("distanceFromPS",distance_from_ps,"regex:RE_DISTANCE_FROM_PS",""),
    ])

    # ── Complainant ───────────────────────────────────────────────────────────
    complainant_cnic       = _first(RE_CNIC,  t) or _first(RE_CNIC,  orig)
    complainant_phone      = _first(RE_PHONE, t) or _first(RE_PHONE_BARE, t) or \
                             _first(RE_PHONE, orig) or _first(RE_PHONE_BARE, orig)
    complainant_age        = _first(RE_AGE,        t)
    complainant_father     = _first(RE_FATHER,     t) or _first(RE_FATHER, orig)
    complainant_caste      = _first(RE_CASTE,      t)
    complainant_profession = _first(RE_PROFESSION, t)
    complainant_address    = _first(RE_RESIDENCE,  t) or _first(RE_RESIDENCE, orig)
    label_complainant      = _first(RE_COMPLAINANT_LABEL, t) or _first(RE_COMPLAINANT_LABEL, orig)

    # ── Accused / Victims / Witnesses ────────────────────────────────────────
    label_accused  = _all(RE_ACCUSED_LABEL,  t) + _all(RE_ACCUSED_LABEL,  orig)
    accused_unknown = bool(RE_ACCUSED_UNKNOWN.search(t) or RE_ACCUSED_UNKNOWN.search(orig))
    label_victims  = _all(RE_VICTIM_LABEL,   t) + _all(RE_VICTIM_LABEL,  orig)
    label_witnesses= _all(RE_WITNESS_LABEL,  t) + _all(RE_WITNESS_LABEL, orig)

    # Deduplicate label lists
    for lst_name in ("label_accused", "label_victims", "label_witnesses"):
        lst = locals()[lst_name]
        seen: set = set()
        deduped = []
        for item in lst:
            if item.lower() not in seen:
                deduped.append(item)
                seen.add(item.lower())
        # Re-assign via a dict trick since we can't assign to locals()
        if lst_name == "label_accused":
            label_accused = deduped
        elif lst_name == "label_victims":
            label_victims = deduped
        else:
            label_witnesses = deduped

    # ── spaCy NER ─────────────────────────────────────────────────────────────
    spacy_ents = _spacy_ents(t)
    extra_persons = [
        p for p in spacy_ents["PERSON"]
        if p.lower() not in (label_complainant or "").lower()
        and p not in label_accused
    ]
    extra_persons_dedup: list = []
    seen_p: set = set()
    for p in extra_persons:
        if p.lower() not in seen_p:
            extra_persons_dedup.append(p)
            seen_p.add(p.lower())
    extra_persons = extra_persons_dedup

    extra_locations = list(dict.fromkeys(
        spacy_ents["GPE"] + spacy_ents["LOC"]
    ))

    complainant_name = label_complainant or (extra_persons[0] if extra_persons else None)

    # ── Legal ─────────────────────────────────────────────────────────────────
    sections = _all(RE_SECTION, t) + _all(RE_SECTION, orig)
    if len(sections) < 2:
        list_sections = [s for s in _all(RE_SECTION_LIST_ITEM, t) if 1 <= int(s) <= 999]
        for s in list_sections:
            if s not in sections:
                sections.append(s)

    expanded = []
    for s in sections:
        for p in re.split(r"[\s,/]+", s):
            if p.strip() and p.strip() not in expanded:
                expanded.append(p.strip())
    sections = expanded

    act        = _first(RE_ACT, t) or _first(RE_ACT, orig)
    rw_match   = RE_SECTION_RW.search(t) or RE_SECTION_RW.search(orig)
    section_rw = f"{rw_match.group(1)} r/w {rw_match.group(2)}" if rw_match else None
    offences   = list(dict.fromkeys(m.lower() for m in _all(RE_OFFENCE, t)))
    if not offences:
        offences = list(dict.fromkeys(m.lower() for m in _all(RE_OFFENCE, orig)))

    # ── Physical evidence ─────────────────────────────────────────────────────
    weapons  = list(dict.fromkeys(m.lower() for m in _all(RE_WEAPON, t)))
    vehicles = list(dict.fromkeys(
        m.lower() for m in (_all(RE_VEHICLE, t) + _all(RE_VEHICLE, orig))
    ))
    plates   = list(dict.fromkeys(_all(RE_PLATE, t) + _all(RE_PLATE, orig)))

    # ── Investigating Officer ─────────────────────────────────────────────────
    io_name       = _first(RE_IO_NAME, t) or _first(RE_IO_NAME, orig)
    io_rank_match = RE_IO_RANK.search(t) or RE_IO_RANK.search(orig)
    io_rank       = io_rank_match.group(1) if io_rank_match else None
    if not io_name and io_rank_match and io_rank_match.group(2):
        io_name = io_rank_match.group(2).strip()
    belt_number = _first(RE_BELT_NUMBER, t) or _first(RE_BELT_NUMBER, orig)

    # ── Hospital ──────────────────────────────────────────────────────────────
    hospital_match = RE_HOSPITAL.search(t) or RE_HOSPITAL.search(orig)
    hospital_ref   = None
    if hospital_match:
        hosp_type    = hospital_match.group(1)
        hosp_no      = hospital_match.group(2)
        hospital_ref = f"{hosp_type} {hosp_no}".strip() if hosp_no else hosp_type

    xai.extend([
        _evidence("complainantName",       complainant_name,       "regex+spacy",            _snippet(t, complainant_name or "")),
        _evidence("complainantFather",     complainant_father,     "regex:RE_FATHER",         _snippet(t, complainant_father or "")),
        _evidence("complainantCNIC",       complainant_cnic,       "regex:RE_CNIC",           _snippet(t, complainant_cnic or "")),
        _evidence("complainantPhone",      complainant_phone,      "regex:RE_PHONE",          _snippet(t, complainant_phone or "")),
        _evidence("complainantAge",        complainant_age,        "regex:RE_AGE",            _snippet(t, complainant_age or "")),
        _evidence("complainantCaste",      complainant_caste,      "regex:RE_CASTE",          _snippet(t, complainant_caste or "")),
        _evidence("complainantProfession", complainant_profession, "regex:RE_PROFESSION",     _snippet(t, complainant_profession or "")),
        _evidence("complainantAddress",    complainant_address,    "regex:RE_RESIDENCE",      _snippet(t, complainant_address or "")),
        _evidence("accusedPersons",        label_accused,          "regex:RE_ACCUSED_LABEL",  ""),
        _evidence("accusedUnknown",        accused_unknown,        "regex:RE_ACCUSED_UNKNOWN",""),
        _evidence("victims",               label_victims,          "regex:RE_VICTIM_LABEL",   ""),
        _evidence("witnesses",             label_witnesses,        "regex:RE_WITNESS_LABEL",  ""),
        _evidence("legalSections",         sections,               "regex:RE_SECTION+list",   ""),
        _evidence("applicableAct",         act,                    "regex:RE_ACT",            _snippet(t, act or "")),
        _evidence("sectionReadWith",       section_rw,             "regex:RE_SECTION_RW",     ""),
        _evidence("offences",              offences,               "regex:RE_OFFENCE",        ""),
        _evidence("weaponsInvolved",       weapons,                "regex:RE_WEAPON",         ""),
        _evidence("vehiclesInvolved",      vehicles,               "regex:RE_VEHICLE",        ""),
        _evidence("vehiclePlates",         plates,                 "regex:RE_PLATE",          ""),
        _evidence("ioName",                io_name,                "regex:RE_IO_NAME",        _snippet(t, io_name or "")),
        _evidence("ioRank",                io_rank,                "regex:RE_IO_RANK",        _snippet(t, io_rank or "")),
        _evidence("beltNumber",            belt_number,            "regex:RE_BELT_NUMBER",    _snippet(t, belt_number or "")),
        _evidence("hospitalReference",     hospital_ref,           "regex:RE_HOSPITAL",       _snippet(t, hospital_ref or "")),
        {"field": "ner_persons",   "extracted_value": extra_persons,   "extraction_method": "spacy", "snippet": "", "confidence": "medium" if extra_persons else "none"},
        {"field": "ner_locations", "extracted_value": extra_locations, "extraction_method": "spacy", "snippet": "", "confidence": "medium" if extra_locations else "none"},
    ])

    # ── Completeness scoring ──────────────────────────────────────────────────
    filled = {
        "firNumber":       bool(fir_no),
        "complainantName": bool(complainant_name),
        "complainantCNIC": bool(complainant_cnic),
        "accused":         bool(label_accused or accused_unknown),
        "dateOfIncident":  bool(date_of_incident),
        "policeStation":   bool(police_station),
        "district":        bool(district),
        "offenceType":     bool(offences),
        "sections":        bool(sections),
        "ioName":          bool(io_name or io_rank),
    }
    completeness = round(sum(filled.values()) / len(filled) * 100, 1)
    missing_core = [k for k, v in filled.items() if not v]

    fields = {
        "firNumber":             fir_no,
        "caseTitle":             _build_case_title(offences, label_accused, incident_place),
        "caseNumber":            fir_no,
        "dateOfReport":          date_of_report,
        "dateOfIncident":        date_of_incident,
        "timeOfIncident":        time_of_incident,
        "policeStation":         police_station,
        "tehsil":                tehsil,
        "district":              district,
        "province":              province,
        "incidentAddress":       incident_place,
        "beat":                  beat,
        "distanceFromPS":        distance_from_ps,
        "complainantName":       complainant_name,
        "complainantFather":     complainant_father,
        "complainantCNIC":       complainant_cnic,
        "complainantAge":        complainant_age,
        "complainantPhone":      complainant_phone,
        "complainantCaste":      complainant_caste,
        "complainantProfession": complainant_profession,
        "complainantAddress":    complainant_address,
        "accusedPersons":        label_accused if label_accused else ([] if not accused_unknown else ["نامعلوم / Unknown"]),
        "accusedUnknown":        accused_unknown,
        "victims":               label_victims,
        "witnesses":             label_witnesses,
        "offenceType":           offences[0] if offences else None,
        "allOffences":           offences,
        "legalSections":         sections,
        "applicableAct":         act,
        "sectionReadWith":       section_rw,
        "weaponsInvolved":       weapons,
        "vehiclesInvolved":      vehicles,
        "vehiclePlates":         plates,
        "hospitalReference":     hospital_ref,
        "investigatingOfficer":  io_name,
        "ioRank":                io_rank,
        "beltNumber":            belt_number,
    }

    return {
        "fields":             fields,
        "completeness_score": completeness,
        "missing_core_fields": missing_core,
        "xai_breakdown": {
            "entities_found": {
                k: (len(v) if isinstance(v, list) else int(bool(v)))
                for k, v in fields.items()
            },
            "evidence":    xai,
            "ner_backend": "spacy",
        },
        "extractedEntities": {
            "persons":   list(filter(None,
                [complainant_name] + (label_accused or []) +
                label_victims + label_witnesses + extra_persons
            )),
            "locations": list(filter(None,
                [incident_place, police_station, district, tehsil] + extra_locations
            )),
            "weapons":   weapons,
            "vehicles":  vehicles,
            "dates":     [d for d in [date_of_incident, date_of_report] if d],
        },
        "missingFields": missing_core,
    }


def _empty_result() -> Dict[str, Any]:
    return {
        "fields":              {},
        "completeness_score":  0.0,
        "missing_core_fields": list(_CRITICAL_FIELD_KEYS),
        "xai_breakdown":       {"entities_found": {}, "evidence": [], "ner_backend": "spacy"},
        "extractedEntities":   {"persons": [], "locations": [], "weapons": [], "vehicles": [], "dates": []},
        "missingFields":       list(_CRITICAL_FIELD_KEYS),
    }


_CRITICAL_FIELD_KEYS = (
    "firNumber", "complainantName", "complainantCNIC", "accused",
    "dateOfIncident", "policeStation", "district", "offenceType",
    "sections", "ioName",
)


def _build_case_title(offences: List[str], accused: List[str], location: Optional[str]) -> str:
    parts: List[str] = []
    if offences:
        parts.append(offences[0].title())
    if accused:
        accused_str = accused[0] if len(accused) == 1 else f"{accused[0]} et al."
        parts.append(f"by {accused_str}")
    elif not accused:
        parts.append("by unknown persons")
    if location:
        loc_short = location[:50].strip().rstrip(",")
        parts.append(f"at {loc_short}")
    return " ".join(parts) if parts else "FIR Case"