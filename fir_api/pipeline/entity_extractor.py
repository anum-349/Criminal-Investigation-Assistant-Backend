from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from pipeline.model_loader import models

logger = logging.getLogger("fir.entities")


# ═════════════════════════════════════════════════════════════════════════════
# Regex patterns — Pakistani FIR format
# ═════════════════════════════════════════════════════════════════════════════
#
# Notes on Urdu regex:
#   • \u0600-\u06FF covers Arabic + Urdu base
#   • Pakistani FIRs are typed in Nastaliq fonts; OCR introduces variation
#     between Arabic-form letters and Urdu-form letters of the same character.
#     The character classes below cover both.
#   • Punjabi/Sindhi loanwords use \u0750-\u077F (extended Arabic).

# ── FIR number — handles NNN/YY, NNN/YYYY, with optional station prefix ─────
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

# Bare "108/25" pattern when it appears after a column label like "نمبر:"
RE_FIR_NO_BARE = re.compile(
    r"(?:^|\n|\s)(\d{2,5}\s*/\s*\d{2,4})(?:\s|$|\n)",
)

# ── Date — Pakistani formats include 22-02-2025, 22/02/2025, ۲۲-۰۲-۲۰۲۵ ─────
RE_DATE = re.compile(
    r"\b(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})\b|"
    r"\b((?:january|february|march|april|may|june|july|august|"
    r"september|october|november|december)\s+\d{1,2}(?:,?\s+\d{4})?)\b|"
    r"\b(\d{1,2}\s+(?:january|february|march|april|may|june|july|august|"
    r"september|october|november|december)(?:,?\s+\d{4})?)\b|"
    r"([\u06F0-\u06F9]{1,2}[\-\/][\u06F0-\u06F9]{1,2}[\-\/][\u06F0-\u06F9]{2,4})",  # Urdu-numeral dates
    re.I,
)

# ── Time — 12:30 PM, 03:50PM, 22:30 hrs, etc. ───────────────────────────────
RE_TIME = re.compile(
    r"\b(\d{1,2}\s*[:.]\s*\d{2}\s*(?:am|pm|hrs?|hours|بجے)?)\b|"
    r"\b(\d{4}\s*(?:hrs?|hours))\b",
    re.I,
)

# ── Police station — handles "ٹھانہ مری", "Thana Saddar", "PS Gulberg" ──────
RE_POLICE_STATION = re.compile(
    r"(?:"
    r"police\s+station|p\.?s\.?|"
    r"thana|"
    r"ٹھانہ|تھانہ|قائمہ"
    r")\s*[:\-]?\s*"
    r"([A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF\s\.]{2,40}?)"
    r"(?=[,\n\r]|district|ضلع|sec|دفعہ|$)",
    re.I,
)

# ── District — "ضلع مری", "District Karachi" ────────────────────────────────
RE_DISTRICT = re.compile(
    r"(?:district|zila|ضلع)\s*[:\-]?\s*"
    r"([A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF\s]{2,30}?)"
    r"(?=[,\n\r]|province|صوبہ|tehsil|تحصیل|$)",
    re.I,
)

# ── Tehsil (sub-district) — common in Pakistani FIRs, missing from v1 ───────
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

# ── CNIC — Pakistani national ID strictly 5-7-1 ─────────────────────────────
RE_CNIC = re.compile(r"\b(\d{5}\s*[\-]?\s*\d{7}\s*[\-]?\s*\d)\b")

# ── Phone — Pakistani mobile (03xx-xxxxxxx) or landline ─────────────────────
RE_PHONE = re.compile(
    r"(?:phone|mobile|contact|tel|cell|موبائل|فون|رابطہ)?\s*[:\-]?\s*"
    r"(\+?92[-\s]?\d{3}[-\s]?\d{7}|"
    r"0\d{3}[-\s]?\d{7}|"
    r"\d{4}[-\s]?\d{7})",
    re.I,
)

# Standalone Pakistani mobile (no label) — fallback
RE_PHONE_BARE = re.compile(r"\b(03\d{2}\s*[\-]?\s*\d{7})\b|\b(\+92\s*3\d{2}\s*\d{7})\b")

# ── Age — "عمر 30 سال", "Age 34", "aged 12" ─────────────────────────────────
RE_AGE = re.compile(r"\b(?:age|aged|عمر)\s*[:\-]?\s*(\d{1,3})\s*(?:years?|سال)?", re.I)

# ── Father / husband — KEY PAKISTANI PATTERN ────────────────────────────────
# "Muhammad Ali s/o Akram" OR "محمد علی ولد اکرم" OR "Saima Bibi w/o Iqbal"
# OR "زوجہ" (wife of), "بنت" (daughter of)
RE_FATHER = re.compile(
    r"\b(?:"
    r"s/?o|d/?o|w/?o|son\s+of|daughter\s+of|wife\s+of|"
    r"ولد(?:یت)?|"        # walad (son of) / waldiyat
    r"بنت|"               # bint (daughter of)
    r"زوجہ|"              # zoja (wife of)
    r"اہلیہ"              # ahliya (wife of, formal)
    r")\s*[:\-]?\s*"
    r"([A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF\s\.]{1,50}?)"
    r"(?=[,\n\r]|age|cnic|عمر|شناختی|موبائل|سکنہ|caste|$)",
    re.I,
)

# ── Caste (ذات) — useful Pakistani FIR field ────────────────────────────────
RE_CASTE = re.compile(
    r"(?:caste|قوم|ذات)\s*[:\-]?\s*"
    r"([A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF\s]{2,30}?)"
    r"(?=[,\n\r]|profession|پیشہ|سکنہ|cnic|شناختی|$)",
    re.I,
)

# ── Profession (پیشہ) — common Pakistani FIR field ─────────────────────────
RE_PROFESSION = re.compile(
    r"(?:profession|occupation|پیشہ|پیشے|peshay?)\s*[:\-]?\s*"
    r"([A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF\s]{2,40}?)"
    r"(?=[,\n\r]|سکنہ|resident|cnic|شناختی|$)",
    re.I,
)

# ── Residence / address — "سکنہ", "resident of", "محلہ" ────────────────────
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

# ── Place of incident with distance from PS (Pakistani-specific) ────────────
# "جائے وقوعہ بمسافت 10 کلومیٹر مشرق ٹھانہ" or "10 km east of PS"
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

# Distance from police station (column 4 typically has this)
RE_DISTANCE_FROM_PS = re.compile(
    r"(?:بمسافت|پر|distance\s+of|approximately)\s*"
    r"(\d+(?:\.\d+)?)\s*"
    r"(کلومیٹر|km|kilometers?|میٹر|m|metre|miles?|میل)"
    r"\s*(شمال|جنوب|مشرق|مغرب|north|south|east|west|northeast|northwest|southeast|southwest)?",
    re.I,
)

# Beat / Police Chowki (sub-station within PS jurisdiction)
RE_BEAT = re.compile(
    r"(?:beat|بیلٹ|پولیس\s*چوکی|chowki|چوکی)\s*[:\-]?\s*"
    r"([A-Za-z\u0600-\u06FF\d][A-Za-z\u0600-\u06FF\d\s\.\-/]{1,30}?)"
    r"(?=[,\n\r]|$)",
    re.I,
)

# ── Person role labels ──────────────────────────────────────────────────────
# Pakistani FIRs use very specific role terms. Multiple persons are common.

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

# Accused — Pakistani FIRs list multiple, often with نامعلوم (unknown)
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

# Specifically catch "نامعلوم" (unknown) accused — common in street crime
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

# ── Legal: sections + acts (Pakistani-specific) ─────────────────────────────
# Handles: "Section 392 PPC", "u/s 302/34 PPC", "دفعہ 302/34 ت پ",
# "Section 392, 397 r/w 7 ATA", "302/34 PPC"
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

# Sections appearing in a numbered list (col 3 of the form, where 324, 148, 149
# might appear on separate lines under a label)
RE_SECTION_LIST_ITEM = re.compile(r"(?:^|\n)\s*(\d{2,4})\s*(?:ت\s*پ|ppc|crpc|ata)?", re.I | re.MULTILINE)

RE_ACT = re.compile(
    r"\b("
    r"(?:pakistan\s+)?penal\s+code|ppc|p\.p\.c\.?|"
    r"تعزیرات\s*پاکستان|ت\s*پ|"
    r"crpc|cr\.p\.c\.?|ضابطہ\s*فوجداری|"
    r"(?:anti[\-\s]?)?terrorism\s+act|ata|انسداد\s*دہشت\s*گردی|"
    r"control\s+of\s+narcotics?\s+substances?\s+act|cnsa|"
    r"hudood\s+ordinance|حدود|"
    r"electronic\s+crimes\s+act|peca|peda"
    r")\b",
    re.I,
)

# "read with" / "متعلقہ" — links sections to act
RE_SECTION_RW = re.compile(
    r"(\d+(?:[\s,/]+\d+)*)\s*(?:r/?w|read\s+with|متعلقہ)\s*(\d+(?:[\s,/]+\d+)*)",
    re.I,
)

# ── Offence type ────────────────────────────────────────────────────────────
# Extended for Pakistani crime taxonomy
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
    r"land\s+dispute|زمین\s*کا\s*تنازعہ"
    r")\b",
    re.I,
)

# ── Weapons (extended for Pakistani context) ────────────────────────────────
RE_WEAPON = re.compile(
    r"\b("
    r"pistol|revolver|rifle|gun|firearm|بندوق|پستول|"
    r"kalashnikov|ak[\-\s]?47|smg|shotgun|کلاشن\s*کوف|"
    r"30[\-\s]?bore|9mm|7\.62|"
    r"knife|dagger|chaqu|چاقو|خنجر|sword|تلوار|axe|کلہاڑی|"
    r"grenade|explosive|bomb|ied|دستی\s*بم|بم|"
    r"baton|stick|lathi|لاٹھی|iron\s+rod|سریا|stone|پتھر|"
    r"acid|تیزاب|"
    r"اسلحہ|اسلحہ\s*ناجائز"      # generic "arms" / "illegal arms"
    r")\b",
    re.I,
)

# ── Vehicles + plates ───────────────────────────────────────────────────────
RE_VEHICLE = re.compile(
    r"\b(motor[\s\-]?cycle|motor[\s\-]?bike|bike|"
    r"موٹر\s*سائیکل|موٹرسائیکل|"
    r"car|sedan|suv|jeep|گاڑی|کار|"
    r"truck|van|bus|pickup|trolley|"
    r"rickshaw|auto|qingqi|چنگچی|رکشہ|"
    r"taxi|cab|"
    r"tractor|ٹریکٹر)\b",
    re.I,
)

RE_PLATE = re.compile(
    r"\b([A-Z]{2,4}[\s\-]?\d{3,5}|"        # standard: LHR-2345
    r"[A-Z]{2,4}[\s\-]?\d{2,3}[\s\-]?[A-Z]?|"
    r"\d{3,4}[\s\-]?[A-Z]{2,4})\b"         # alternate: 2345 LHR
)

# ── Investigating Officer rank + name ───────────────────────────────────────
# Pakistani police hierarchy: IGP > AIGP > DIG > SSP > SP > DSP > ASP > Insp > SI > ASI > HC > Constable
RE_IO_RANK = re.compile(
    r"\b("
    r"IGP|AIGP|DIG|SSP|SP|DSP|ASP|"
    r"inspector|insp|sub[\-\s]?inspector|si|"
    r"assistant\s+sub[\-\s]?inspector|asi|"
    r"head\s+constable|hc|"
    r"constable|"
    r"sho|"
    r"انسپکٹر|سب\s*انسپکٹر|اسسٹنٹ\s*سب\s*انسپکٹر|ہیڈ\s*کانسٹیبل|کانسٹیبل|افسر|تفتیشی\s*افسر"
    r")\b\.?\s*"
    r"([A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF\s\.]{2,40}?)?"
    r"(?=[,\n\r]|signature|دستخط|belt|$)",
    re.I,
)

# IO name preceded by clear "I.O." label
RE_IO_NAME = re.compile(
    r"(?:i\.?o\.?|investigating\s+officer|تفتیشی\s*افسر|انچارج)"
    r"\s*[:\-]?\s*"
    r"([A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF\s\.]{2,40}?)"
    r"(?=[,\n\r]|signature|دستخط|$)",
    re.I,
)

# Belt number — common in Pakistani FIRs (officer's badge/posting number)
RE_BELT_NUMBER = re.compile(
    r"(?:belt|بیلٹ\s*نمبر|بیلٹ)\s*(?:no\.?|number|نمبر)?\s*[:\-]?\s*"
    r"(\d{2,5}\s*[/]?\s*[A-Z]?)",
    re.I,
)

# ── Hospital / MLC reference (common in injury FIRs) ────────────────────────
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
    out, seen = [], set()
    for m in pat.finditer(text):
        for g in m.groups():
            if g and g.strip():
                v = g.strip()
                key = v.lower()
                if key not in seen:
                    out.append(v)
                    seen.add(key)
                break
    return out


def _snippet(text: str, value: str, window: int = 30) -> str:
    if not value:
        return ""
    idx = text.lower().find(value.lower())
    if idx < 0:
        return value
    start, end = max(0, idx - window), min(len(text), idx + len(value) + window)
    return text[start:end].replace("\n", " ").strip()


def _evidence(field_name: str, value: Any, method: str, snippet: str = "") -> Dict:
    return {
        "field":             field_name,
        "extracted_value":   value,
        "extraction_method": method,
        "snippet":           snippet[:120],
        "confidence":        "high" if value else "none",
    }


def _normalise_urdu_digits(text: str) -> str:
    """Convert Urdu-Persian digits (۰-۹) to ASCII (0-9). FIRs sometimes mix
    both — normalising lets the date/CNIC/phone regexes catch them."""
    table = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    return text.translate(table)


# ═════════════════════════════════════════════════════════════════════════════
# NER backends
# ═════════════════════════════════════════════════════════════════════════════

def _spacy_persons_locations(text: str) -> Dict[str, List[str]]:
    nlp = models.spacy_nlp
    if nlp is None:
        return {"PERSON": [], "GPE": [], "LOC": [], "ORG": []}
    try:
        doc = nlp(text[:50_000])
    except Exception as e:
        logger.warning("spaCy NER failed: %s", e)
        return {"PERSON": [], "GPE": [], "LOC": [], "ORG": []}

    buckets: Dict[str, List[str]] = {"PERSON": [], "GPE": [], "LOC": [], "ORG": []}
    seen: Dict[str, set] = {k: set() for k in buckets}
    for ent in doc.ents:
        if ent.label_ in buckets:
            v = ent.text.strip()
            if v and v.lower() not in seen[ent.label_] and len(v) > 1:
                buckets[ent.label_].append(v)
                seen[ent.label_].add(v.lower())
    return buckets


def _urdu_ner_persons_locations(text: str) -> Dict[str, List[str]]:
    pipe = models.urdu_ner_pipeline
    if pipe is None:
        return {"PERSON": [], "GPE": [], "LOC": [], "ORG": []}
    try:
        # mBERT max position = 512 tokens; chunk if longer
        entities = pipe(text[:5000])
    except Exception as e:
        logger.warning("Urdu NER failed: %s", e)
        return {"PERSON": [], "GPE": [], "LOC": [], "ORG": []}

    buckets = {"PERSON": [], "GPE": [], "LOC": [], "ORG": []}
    seen: Dict[str, set] = {k: set() for k in buckets}
    label_map = {
        "PER": "PERSON", "PERSON": "PERSON",
        "LOC": "LOC", "LOCATION": "LOC",
        "GPE": "GPE",
        "ORG": "ORG", "ORGANIZATION": "ORG",
    }
    for ent in entities:
        raw_label = (ent.get("entity_group") or ent.get("entity", "")).split("-")[-1]
        label = label_map.get(raw_label.upper())
        if not label:
            continue
        v = (ent.get("word") or "").strip()
        if v and v.lower() not in seen[label]:
            buckets[label].append(v)
            seen[label].add(v.lower())
    return buckets


# ═════════════════════════════════════════════════════════════════════════════
# Public API
# ═════════════════════════════════════════════════════════════════════════════

def extract_entities(text: str, original_text: Optional[str] = None) -> Dict[str, Any]:
    """Pakistani-FIR-tuned extraction. Returns the canonical envelope the
    orchestrator and frontend consume."""
    if not text or not text.strip():
        return _empty_result()

    # Normalise digits once for the whole pipeline so regex hits both scripts
    t = _normalise_urdu_digits(text)
    orig = _normalise_urdu_digits(original_text) if original_text else None

    xai: List[Dict] = []

    # ── Header / FIR identifier ─────────────────────────────────────────────
    fir_no = _first(RE_FIR_NO, t) or _first(RE_FIR_NO_BARE, t)
    xai.append(_evidence("firNumber", fir_no, "regex:RE_FIR_NO", _snippet(t, fir_no or "")))

    # ── Dates: try to capture BOTH date-of-report and date-of-incident ──────
    all_dates = _all(RE_DATE, t)
    # Heuristic: first date is usually date-of-report (top of form),
    # later dates within narrative are date-of-incident.
    date_of_report   = all_dates[0] if all_dates else None
    date_of_incident = all_dates[1] if len(all_dates) > 1 else all_dates[0] if all_dates else None
    all_times = _all(RE_TIME, t)
    time_of_incident = all_times[0] if all_times else None

    xai.append(_evidence("dateOfReport",     date_of_report,    "regex:RE_DATE[0]", ""))
    xai.append(_evidence("dateOfIncident",   date_of_incident,  "regex:RE_DATE[1]", ""))
    xai.append(_evidence("timeOfIncident",   time_of_incident,  "regex:RE_TIME[0]", ""))

    # ── Location hierarchy ──────────────────────────────────────────────────
    police_station = _first(RE_POLICE_STATION, t)
    district       = _first(RE_DISTRICT, t)
    tehsil         = _first(RE_TEHSIL, t)
    province       = _first(RE_PROVINCE, t)
    incident_place = _first(RE_INCIDENT_PLACE, t)
    beat           = _first(RE_BEAT, t)

    # Distance + direction from PS
    dist_m = RE_DISTANCE_FROM_PS.search(t)
    distance_from_ps = None
    if dist_m:
        value, unit, direction = dist_m.group(1), dist_m.group(2), (dist_m.group(3) or "").strip()
        distance_from_ps = f"{value} {unit}" + (f" {direction}" if direction else "")

    xai.extend([
        _evidence("policeStation",    police_station,   "regex:RE_POLICE_STATION", _snippet(t, police_station or "")),
        _evidence("district",         district,         "regex:RE_DISTRICT",       _snippet(t, district or "")),
        _evidence("tehsil",           tehsil,           "regex:RE_TEHSIL",         _snippet(t, tehsil or "")),
        _evidence("province",         province,         "regex:RE_PROVINCE",       _snippet(t, province or "")),
        _evidence("incidentAddress",  incident_place,   "regex:RE_INCIDENT_PLACE", _snippet(t, incident_place or "")),
        _evidence("beat",             beat,             "regex:RE_BEAT",           _snippet(t, beat or "")),
        _evidence("distanceFromPS",   distance_from_ps, "regex:RE_DISTANCE_FROM_PS", ""),
    ])

    # ── Complainant identity block (column 2 of form 24.5(1)) ───────────────
    complainant_cnic      = _first(RE_CNIC, t)
    complainant_phone     = _first(RE_PHONE, t) or _first(RE_PHONE_BARE, t)
    complainant_age       = _first(RE_AGE, t)
    complainant_father    = _first(RE_FATHER, t)
    complainant_caste     = _first(RE_CASTE, t)
    complainant_profession = _first(RE_PROFESSION, t)
    complainant_address   = _first(RE_RESIDENCE, t)

    label_complainant = _first(RE_COMPLAINANT_LABEL, t)

    # ── Persons by explicit role label ──────────────────────────────────────
    label_accused   = _all(RE_ACCUSED_LABEL, t)
    accused_unknown = bool(RE_ACCUSED_UNKNOWN.search(t))
    label_victims   = _all(RE_VICTIM_LABEL, t)
    label_witnesses = _all(RE_WITNESS_LABEL, t)

    # ── NER backends fill in names that lack explicit labels ────────────────
    spacy_ents = _spacy_persons_locations(t)
    urdu_ents  = _urdu_ner_persons_locations(orig) if orig else \
                 {"PERSON": [], "GPE": [], "LOC": [], "ORG": []}

    all_labelled_persons = set()
    for v in [label_complainant] + label_accused + label_victims + label_witnesses:
        if v:
            for token in v.split():
                if len(token) > 2:
                    all_labelled_persons.add(token.lower())

    extra_persons = []
    for p in spacy_ents["PERSON"] + urdu_ents["PERSON"]:
        if p and not any(t.lower() in all_labelled_persons for t in p.split() if len(t) > 2):
            if p not in extra_persons:
                extra_persons.append(p)

    extra_locations = list(dict.fromkeys(
        spacy_ents["GPE"] + spacy_ents["LOC"] + urdu_ents["GPE"] + urdu_ents["LOC"]
    ))

    # Resolve complainant: prefer explicit label, fall back to first NER person
    complainant_name = label_complainant or (extra_persons[0] if extra_persons else None)

    # ── Legal: sections, act, "read with" linkages ──────────────────────────
    sections = _all(RE_SECTION, t)
    # If form had sections as a numbered list (e.g., column 3 in form 24.5(1)),
    # try the list-item pattern too
    if len(sections) < 2:
        list_sections = _all(RE_SECTION_LIST_ITEM, t)
        # Only keep 2-3 digit numbers that look like section numbers (filter out years, CNIC fragments)
        list_sections = [s for s in list_sections if 1 <= int(s) <= 999]
        for s in list_sections:
            if s not in sections:
                sections.append(s)

    # Split combined sections like "324/148/149" into individual numbers
    expanded = []
    for s in sections:
        parts = re.split(r"[\s,/]+", s)
        for p in parts:
            if p.strip() and p.strip() not in expanded:
                expanded.append(p.strip())
    sections = expanded

    act = _first(RE_ACT, t)
    rw_match = RE_SECTION_RW.search(t)
    section_rw = f"{rw_match.group(1)} r/w {rw_match.group(2)}" if rw_match else None

    offences = list(dict.fromkeys(m.lower() for m in _all(RE_OFFENCE, t)))

    # ── Physical evidence ───────────────────────────────────────────────────
    weapons  = list(dict.fromkeys(m.lower() for m in _all(RE_WEAPON, t)))
    vehicles = list(dict.fromkeys(m.lower() for m in _all(RE_VEHICLE, t)))
    plates   = _all(RE_PLATE, t)

    # ── Investigating Officer ───────────────────────────────────────────────
    io_name = _first(RE_IO_NAME, t)
    io_rank_match = RE_IO_RANK.search(t)
    io_rank = io_rank_match.group(1) if io_rank_match else None
    if not io_name and io_rank_match and io_rank_match.group(2):
        io_name = io_rank_match.group(2).strip()
    belt_number = _first(RE_BELT_NUMBER, t)

    # ── Hospital reference (injury / death cases) ───────────────────────────
    hospital_match = RE_HOSPITAL.search(t)
    hospital_ref = None
    if hospital_match:
        hosp_type = hospital_match.group(1)
        hosp_no = hospital_match.group(2)
        hospital_ref = f"{hosp_type} {hosp_no}".strip() if hosp_no else hosp_type

    xai.extend([
        _evidence("complainantName",     complainant_name,       "regex+ner",                  _snippet(t, complainant_name or "")),
        _evidence("complainantFather",   complainant_father,     "regex:RE_FATHER",            _snippet(t, complainant_father or "")),
        _evidence("complainantCNIC",     complainant_cnic,       "regex:RE_CNIC",              _snippet(t, complainant_cnic or "")),
        _evidence("complainantPhone",    complainant_phone,      "regex:RE_PHONE",             _snippet(t, complainant_phone or "")),
        _evidence("complainantAge",      complainant_age,        "regex:RE_AGE",               _snippet(t, complainant_age or "")),
        _evidence("complainantCaste",    complainant_caste,      "regex:RE_CASTE",             _snippet(t, complainant_caste or "")),
        _evidence("complainantProfession", complainant_profession, "regex:RE_PROFESSION",      _snippet(t, complainant_profession or "")),
        _evidence("complainantAddress",  complainant_address,    "regex:RE_RESIDENCE",         _snippet(t, complainant_address or "")),
        _evidence("accusedPersons",      label_accused,          "regex:RE_ACCUSED_LABEL",     ""),
        _evidence("accusedUnknown",      accused_unknown,        "regex:RE_ACCUSED_UNKNOWN",   ""),
        _evidence("victims",             label_victims,          "regex:RE_VICTIM_LABEL",      ""),
        _evidence("witnesses",           label_witnesses,        "regex:RE_WITNESS_LABEL",     ""),
        _evidence("legalSections",       sections,               "regex:RE_SECTION + list",    ""),
        _evidence("applicableAct",       act,                    "regex:RE_ACT",               _snippet(t, act or "")),
        _evidence("sectionReadWith",     section_rw,             "regex:RE_SECTION_RW",        ""),
        _evidence("offences",            offences,               "regex:RE_OFFENCE",           ""),
        _evidence("weaponsInvolved",     weapons,                "regex:RE_WEAPON",            ""),
        _evidence("vehiclesInvolved",    vehicles,               "regex:RE_VEHICLE",           ""),
        _evidence("vehiclePlates",       plates,                 "regex:RE_PLATE",             ""),
        _evidence("ioName",              io_name,                "regex:RE_IO_NAME",           _snippet(t, io_name or "")),
        _evidence("ioRank",              io_rank,                "regex:RE_IO_RANK",           _snippet(t, io_rank or "")),
        _evidence("beltNumber",          belt_number,            "regex:RE_BELT_NUMBER",       _snippet(t, belt_number or "")),
        _evidence("hospitalReference",   hospital_ref,           "regex:RE_HOSPITAL",          _snippet(t, hospital_ref or "")),
        {"field": "ner_persons",   "extracted_value": extra_persons,   "extraction_method": "spacy + urdu_ner", "snippet": "", "confidence": "medium" if extra_persons else "none"},
        {"field": "ner_locations", "extracted_value": extra_locations, "extraction_method": "spacy + urdu_ner", "snippet": "", "confidence": "medium" if extra_locations else "none"},
    ])

    # ── Completeness scoring (Pakistani-FIR critical fields) ────────────────
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

    # ── Pack fields for payload_generator ───────────────────────────────────
    fields = {
        # Identifiers
        "firNumber":          fir_no,
        "caseTitle":          _build_case_title(offences, label_accused, incident_place),
        "caseNumber":         fir_no,

        # Dates
        "dateOfReport":       date_of_report,
        "dateOfIncident":     date_of_incident,
        "timeOfIncident":     time_of_incident,

        # Location hierarchy
        "policeStation":      police_station,
        "tehsil":             tehsil,
        "district":           district,
        "province":           province,
        "incidentAddress":    incident_place,
        "beat":               beat,
        "distanceFromPS":     distance_from_ps,

        # Complainant (full Pakistani identity block)
        "complainantName":      complainant_name,
        "complainantFather":    complainant_father,
        "complainantCNIC":      complainant_cnic,
        "complainantAge":       complainant_age,
        "complainantPhone":     complainant_phone,
        "complainantCaste":     complainant_caste,
        "complainantProfession": complainant_profession,
        "complainantAddress":   complainant_address,

        # Other persons
        "accusedPersons":     label_accused if label_accused else ([] if not accused_unknown else ["نامعلوم / Unknown"]),
        "accusedUnknown":     accused_unknown,
        "victims":            label_victims,
        "witnesses":          label_witnesses,

        # Legal
        "offenceType":        offences[0] if offences else None,
        "allOffences":        offences,
        "legalSections":      sections,
        "applicableAct":      act,
        "sectionReadWith":    section_rw,

        # Evidence
        "weaponsInvolved":    weapons,
        "vehiclesInvolved":   vehicles,
        "vehiclePlates":      plates,
        "hospitalReference":  hospital_ref,

        # Police / IO
        "investigatingOfficer": io_name,
        "ioRank":               io_rank,
        "beltNumber":           belt_number,
    }

    return {
        "fields":               fields,
        "completeness_score":   completeness,
        "missing_core_fields":  missing_core,
        "xai_breakdown": {
            "entities_found": {k: (len(v) if isinstance(v, list) else int(bool(v))) for k, v in fields.items()},
            "evidence":       xai,
            "ner_backends": {
                "spacy_ready":    models.spacy_nlp is not None,
                "urdu_ner_ready": models.urdu_ner_pipeline is not None,
            },
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
        "xai_breakdown":       {"entities_found": {}, "evidence": [], "ner_backends": {}},
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