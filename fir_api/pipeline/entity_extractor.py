"""
pipeline/entity_extractor.py
────────────────────────────
Extract structured entities from FIR text.

Hybrid strategy:
    1. **Regex** for fields that follow strict formats:
         FIR number, CNIC, phone, dates, sections, plate numbers.
       These are *always* better matched by regex than ML — they're
       deterministic and labelled with field names in the source document.

    2. **spaCy NER** on the English (translated) text for free-text entities:
         PERSON  → complainant / accused / witness candidates
         GPE/LOC → districts, cities, areas
         ORG     → police stations, agencies
       spaCy's `en_core_web_sm` covers these out of the box.

    3. **Transformer NER** (mBERT fine-tuned on Urdu) on the *original* Urdu
       text when available. This catches entities that translation might
       distort (Urdu personal names get mangled by Marian — keeping them in
       Urdu preserves the exact spelling for matching against suspect DBs).

    4. **Field disambiguation**: combine label proximity (e.g., the word
       "complainant" precedes a name) with NER tags to assign each PERSON
       to the right role (complainant vs accused vs witness).

XAI:
    Every extracted value carries:
       • field name
       • the rule / model that produced it ("regex:RE_FIR_NO", "spacy:PERSON",
         "urdu_ner:B-PER")
       • the source snippet (±20 chars of context)
       • a confidence score
    The frontend XAI panel renders these as evidence cards.

Output shape is identical to the old extractor so the orchestrator and
payload_generator don't need to change.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from pipeline.model_loader import models

logger = logging.getLogger("fir.entities")


# ═════════════════════════════════════════════════════════════════════════════
# Regex patterns — structured fields
# ═════════════════════════════════════════════════════════════════════════════

# FIR / case number
RE_FIR_NO = re.compile(
    r"(?:fir|case|maqdam|mukadma|ایف\s*آئی\s*آر|مقدمہ)\s*"
    r"(?:number|no\.?|#|نمبر)?\s*[:\-]?\s*(\d+\s*[\/\-]?\s*\d{2,4})",
    re.I,
)

# Date — covers 15/03/2024, 15-03-24, March 15 2024, 15 March 2024
RE_DATE = re.compile(
    r"\b(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})\b|"
    r"\b((?:january|february|march|april|may|june|july|august|"
    r"september|october|november|december)\s+\d{1,2}(?:,?\s+\d{4})?)\b|"
    r"\b(\d{1,2}\s+(?:january|february|march|april|may|june|july|august|"
    r"september|october|november|december)(?:,?\s+\d{4})?)\b",
    re.I,
)

# Time — 10:30 PM, 2200 hrs, 8 baje
RE_TIME = re.compile(
    r"\b(\d{1,2}\s*[:.]\s*\d{2}\s*(?:am|pm|hrs|hours)?)\b|"
    r"\b(\d{4}\s*(?:hrs|hours))\b",
    re.I,
)

# Police station / thana
RE_POLICE_STATION = re.compile(
    r"(?:police\s*station|thana|تھانہ)\s*[:\-]?\s*([A-Za-z][A-Za-z\s\u0600-\u06FF\.]{2,40}?)"
    r"(?=[,\n\r]|district|ضلع|$)",
    re.I,
)

# District
RE_DISTRICT = re.compile(
    r"(?:district|zila|ضلع)\s*[:\-]?\s*([A-Za-z][A-Za-z\s\u0600-\u06FF]{2,30}?)"
    r"(?=[,\n\r]|province|صوبہ|$)",
    re.I,
)

# Province
RE_PROVINCE = re.compile(
    r"(?:province|suba|صوبہ)\s*[:\-]?\s*("
    r"punjab|sindh|balochistan|kpk|khyber\s+pakhtunkhwa|"
    r"پنجاب|سندھ|بلوچستان|خیبر\s*پختونخوا)",
    re.I,
)

# Address / place of incident
RE_INCIDENT_PLACE = re.compile(
    r"(?:place\s+of\s+incident|incident\s+address|location|near|"
    r"maqam[-\s]?(?:e[-\s]?)?waqia|مقام\s*واقعہ)\s*[:\-]?\s*"
    r"(.{5,120}?)(?=\n|complainant|accused|مدعی|ملزم|$)",
    re.I,
)

# CNIC — strict 5-7-1 pattern
RE_CNIC = re.compile(r"\b(\d{5}\s*[\-]?\s*\d{7}\s*[\-]?\s*\d)\b")

# Phone — Pakistani formats
RE_PHONE = re.compile(
    r"(?:phone|mobile|contact|tel|cell)\s*[:\-]?\s*"
    r"(\+?92[-\s]?\d{3}[-\s]?\d{7}|0\d{3}[-\s]?\d{7}|\d{4}[-\s]?\d{7})",
    re.I,
)

# Age
RE_AGE = re.compile(r"\b(?:age|aged|عمر)\s*[:\-]?\s*(\d{1,3})\s*(?:years?|سال)?", re.I)

# Father name (s/o, d/o, w/o, son of, ولدیت)
RE_FATHER = re.compile(
    r"\b(?:s/?o|d/?o|w/?o|son\s+of|daughter\s+of|wife\s+of|father[:\s]+|ولدیت\s*[:\-]?\s*)"
    r"([A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF\s\.]{1,50}?)(?=[,\n\r]|age|cnic|$)",
    re.I,
)

# Person role labels — used both for regex and to disambiguate NER hits
RE_COMPLAINANT_LABEL = re.compile(
    r"(?:complainant|petitioner|maddai|shaki|reporter|مدعی|شاکی)\s*[:\-]?\s*"
    r"([A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF\s\.]{1,60}?)"
    r"(?=[,\n\r]|father|s/o|d/o|w/o|cnic|age|$)",
    re.I,
)
RE_ACCUSED_LABEL = re.compile(
    r"(?:^|\n|\.)\s*(?:accused|suspect|mulzim|culprit|ملزم|مشتبہ)\s*[:\-]?\s*"
    r"([A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF\s\.]{1,60}?)"
    r"(?=[,\n\r]|age|alias|s/o|d/o|cnic|$)",
    re.I | re.MULTILINE,
)
RE_VICTIM_LABEL = re.compile(
    r"(?:victim|injured|mutassira|متاثرہ|متاثر)\s*[:\-]?\s*"
    r"([A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF\s\.]{1,60}?)"
    r"(?=[,\n\r]|age|s/o|d/o|$)",
    re.I,
)
RE_WITNESS_LABEL = re.compile(
    r"(?:witness|eyewitness|gawah|گواہ)\s*[:\-]?\s*"
    r"([A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF\s\.]{1,60}?)"
    r"(?=[,\n\r]|age|s/o|d/o|$)",
    re.I,
)

# Legal — sections + acts
RE_SECTION = re.compile(
    r"(?:section|u/s|under\s+section|dafaa|دفعہ)\s*"
    r"(\d+(?:\s*[\/,]\s*\d+)*)",
    re.I,
)
RE_ACT = re.compile(
    r"(?:under|of)\s+("
    r"(?:pakistan\s+)?penal\s+code|ppc|crpc|"
    r"(?:anti[\-\s]?)?terrorism\s+act|ata|"
    r"control\s+of\s+narcotics?\s+substances?\s+act|cnsa"
    r")",
    re.I,
)

# Offence type (covers common Pakistan-context crimes)
RE_OFFENCE = re.compile(
    r"\b("
    r"murder|qatal|homicide|"
    r"robbery|dakaiti|loot(?:ing|ed)?|"
    r"theft|chori|burglary|"
    r"kidnap(?:ping)?|abduction|"
    r"rape|sexual\s+assault|"
    r"assault|battery|"
    r"fraud|forgery|cheating|"
    r"arson|"
    r"terrorism|extremism|"
    r"smuggling|narcotics?|drug\s+trafficking|"
    r"extortion|bhatta|"
    r"attempt(?:ed)?\s+(?:to\s+)?murder|qatal\s+amad"
    r")\b",
    re.I,
)

# Weapons
RE_WEAPON = re.compile(
    r"\b("
    r"pistol|revolver|rifle|gun|firearm|"
    r"kalashnikov|ak[\-\s]?47|smg|shotgun|"
    r"knife|dagger|chaqu|sword|axe|"
    r"grenade|explosive|bomb|ied|"
    r"baton|stick|lathi|iron\s+rod|stone"
    r")\b",
    re.I,
)

# Vehicles + plates
RE_VEHICLE = re.compile(
    r"\b(motor[\s\-]?cycle|motor[\s\-]?bike|bike|car|sedan|suv|jeep|"
    r"truck|van|bus|pickup|rickshaw|auto|taxi|cab)\b",
    re.I,
)
RE_PLATE = re.compile(r"\b([A-Z]{2,4}[\s\-]?\d{3,5}|[A-Z]{2,4}[\s\-]?\d{2,3}[\s\-]?[A-Z]?)\b")


# ═════════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════════

def _first(pat: re.Pattern, text: str) -> Optional[str]:
    m = pat.search(text)
    if not m:
        return None
    # Find the first non-empty captured group
    for g in m.groups():
        if g and g.strip():
            return g.strip()
    return None


def _all(pat: re.Pattern, text: str) -> List[str]:
    out: List[str] = []
    seen = set()
    for m in pat.finditer(text):
        for g in m.groups():
            if g and g.strip():
                v = g.strip()
                if v.lower() not in seen:
                    out.append(v)
                    seen.add(v.lower())
                break
    return out


def _snippet(text: str, value: str, window: int = 30) -> str:
    """Return ±window chars around the first occurrence of `value` for XAI."""
    if not value:
        return ""
    idx = text.lower().find(value.lower())
    if idx < 0:
        return value
    start = max(0, idx - window)
    end   = min(len(text), idx + len(value) + window)
    return text[start:end].replace("\n", " ").strip()


def _evidence(field_name: str, value: Any, method: str, snippet: str = "") -> Dict:
    return {
        "field":             field_name,
        "extracted_value":   value,
        "extraction_method": method,
        "snippet":           snippet[:120],
        "confidence":        "high" if value else "none",
    }


# ═════════════════════════════════════════════════════════════════════════════
# NER backends
# ═════════════════════════════════════════════════════════════════════════════

def _spacy_persons_locations(text: str) -> Dict[str, List[str]]:
    """Run spaCy on the English text. Returns deduplicated lists per NER label."""
    nlp = models.spacy_nlp
    if nlp is None:
        return {"PERSON": [], "GPE": [], "LOC": [], "ORG": []}

    try:
        doc = nlp(text[:50_000])  # cap to avoid memory blow-ups on huge docs
    except Exception as e:
        logger.warning("spaCy NER failed: %s", e)
        return {"PERSON": [], "GPE": [], "LOC": [], "ORG": []}

    buckets: Dict[str, List[str]] = {"PERSON": [], "GPE": [], "LOC": [], "ORG": []}
    seen: Dict[str, set] = {k: set() for k in buckets}
    for ent in doc.ents:
        label = ent.label_
        if label in buckets:
            v = ent.text.strip()
            if v and v.lower() not in seen[label] and len(v) > 1:
                buckets[label].append(v)
                seen[label].add(v.lower())
    return buckets


def _urdu_ner_persons_locations(text: str) -> Dict[str, List[str]]:
    """Run the Urdu transformer NER on the *original* Urdu text.

    This is the recommended path when input is Urdu — we don't lose names
    in translation. The model emits B-PER/I-PER/B-LOC/I-LOC/B-ORG/I-ORG
    tags; aggregation_strategy="simple" already merges these into spans.
    """
    pipe = models.urdu_ner_pipeline
    if pipe is None:
        return {"PERSON": [], "GPE": [], "LOC": [], "ORG": []}

    try:
        entities = pipe(text[:5000])  # mBERT max position is 512 tokens
    except Exception as e:
        logger.warning("Urdu NER failed: %s", e)
        return {"PERSON": [], "GPE": [], "LOC": [], "ORG": []}

    buckets = {"PERSON": [], "GPE": [], "LOC": [], "ORG": []}
    seen: Dict[str, set] = {k: set() for k in buckets}
    # Different Urdu NER models use different tag schemes — normalise:
    label_map = {
        "PER": "PERSON", "PERSON": "PERSON",
        "LOC": "LOC",    "LOCATION": "LOC",
        "GPE": "GPE",
        "ORG": "ORG", "ORGANIZATION": "ORG",
    }
    for ent in entities:
        raw_label = ent.get("entity_group") or ent.get("entity", "")
        # Strip B-/I- prefix if aggregation didn't apply
        raw_label = raw_label.split("-")[-1]
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
    """Main entity-extraction entry point.

    Args:
        text:          The English (translated, if needed) text — most regex
                       is tuned for this.
        original_text: The original (possibly Urdu) text. If provided and the
                       Urdu NER model is loaded, person names from this take
                       precedence over their Marian-translated versions.

    Returns:
        Dict with: fields, completeness_score, missing_core_fields,
        extractedEntities, xai_breakdown — matching the schema the existing
        orchestrator and frontend already consume.
    """
    t = text or ""
    if not t.strip():
        return _empty_result()

    xai: List[Dict] = []

    # ── Structured fields via regex ─────────────────────────────────────────
    fir_no         = _first(RE_FIR_NO, t);          xai.append(_evidence("firNumber",        fir_no,         "regex:RE_FIR_NO",         _snippet(t, fir_no or "")))
    incident_date  = _first(RE_DATE, t);            xai.append(_evidence("dateOfIncident",   incident_date,  "regex:RE_DATE",           _snippet(t, incident_date or "")))
    incident_time  = _first(RE_TIME, t);            xai.append(_evidence("timeOfIncident",   incident_time,  "regex:RE_TIME",           _snippet(t, incident_time or "")))
    police_station = _first(RE_POLICE_STATION, t);  xai.append(_evidence("policeStation",    police_station, "regex:RE_POLICE_STATION", _snippet(t, police_station or "")))
    district       = _first(RE_DISTRICT, t);        xai.append(_evidence("district",         district,       "regex:RE_DISTRICT",       _snippet(t, district or "")))
    province       = _first(RE_PROVINCE, t);        xai.append(_evidence("province",         province,       "regex:RE_PROVINCE",       _snippet(t, province or "")))
    address        = _first(RE_INCIDENT_PLACE, t);  xai.append(_evidence("incidentAddress",  address,        "regex:RE_INCIDENT_PLACE", _snippet(t, address or "")))
    complainant_cnic   = _first(RE_CNIC, t);        xai.append(_evidence("complainantCNIC",  complainant_cnic, "regex:RE_CNIC", _snippet(t, complainant_cnic or "")))
    complainant_phone  = _first(RE_PHONE, t);       xai.append(_evidence("complainantPhone", complainant_phone, "regex:RE_PHONE", _snippet(t, complainant_phone or "")))
    complainant_age    = _first(RE_AGE, t);         xai.append(_evidence("complainantAge",   complainant_age, "regex:RE_AGE", _snippet(t, complainant_age or "")))
    complainant_father = _first(RE_FATHER, t);      xai.append(_evidence("complainantFather", complainant_father, "regex:RE_FATHER", _snippet(t, complainant_father or "")))

    # ── Person role labels (regex first — these have explicit prefixes) ─────
    label_complainant = _first(RE_COMPLAINANT_LABEL, t)
    label_accused     = _all(RE_ACCUSED_LABEL, t)
    label_victims     = _all(RE_VICTIM_LABEL, t)
    label_witnesses   = _all(RE_WITNESS_LABEL, t)

    # ── NER backends ────────────────────────────────────────────────────────
    spacy_ents = _spacy_persons_locations(t)
    urdu_ents  = _urdu_ner_persons_locations(original_text) if original_text else {"PERSON": [], "GPE": [], "LOC": [], "ORG": []}

    # Merge person candidates from NER that weren't already labelled
    extra_persons = []
    for p in spacy_ents["PERSON"] + urdu_ents["PERSON"]:
        if p and not any(p.lower() in (label or "").lower() for label in
                         ([label_complainant] + label_accused + label_victims + label_witnesses)):
            if p not in extra_persons:
                extra_persons.append(p)

    # NER locations augment regex misses
    extra_locations = list(dict.fromkeys(
        spacy_ents["GPE"] + spacy_ents["LOC"] + urdu_ents["GPE"] + urdu_ents["LOC"]
    ))

    xai.append({
        "field":             "ner_persons",
        "extracted_value":   extra_persons,
        "extraction_method": "spacy:PERSON + urdu_ner:PER",
        "snippet":           "",
        "confidence":        "medium" if extra_persons else "none",
    })
    xai.append({
        "field":             "ner_locations",
        "extracted_value":   extra_locations,
        "extraction_method": "spacy:GPE/LOC + urdu_ner:LOC",
        "snippet":           "",
        "confidence":        "medium" if extra_locations else "none",
    })

    # ── Legal ───────────────────────────────────────────────────────────────
    sections = _all(RE_SECTION, t)
    act      = _first(RE_ACT, t)
    offences = list(dict.fromkeys(m.lower() for m in _all(RE_OFFENCE, t)))
    xai.append(_evidence("legalSections", sections, "regex:RE_SECTION", ""))
    xai.append(_evidence("applicableAct", act,      "regex:RE_ACT",     _snippet(t, act or "")))
    xai.append(_evidence("offences",      offences, "regex:RE_OFFENCE", ""))

    # ── Physical evidence ───────────────────────────────────────────────────
    weapons  = list(dict.fromkeys(m.lower() for m in _all(RE_WEAPON, t)))
    vehicles = list(dict.fromkeys(m.lower() for m in _all(RE_VEHICLE, t)))
    plates   = list(dict.fromkeys(_all(RE_PLATE, t)))
    xai.append(_evidence("weaponsInvolved",   weapons,  "regex:RE_WEAPON",  ""))
    xai.append(_evidence("vehiclesInvolved",  vehicles, "regex:RE_VEHICLE", ""))
    xai.append(_evidence("vehiclePlates",     plates,   "regex:RE_PLATE",   ""))

    # ── Resolve final field values ──────────────────────────────────────────
    # Complainant: prefer the explicit label, fall back to the first NER person
    complainant_name = label_complainant or (extra_persons[0] if extra_persons else None)
    xai.append(_evidence("complainantName", complainant_name,
                         "regex:RE_COMPLAINANT_LABEL" if label_complainant else "spacy:PERSON[0]",
                         _snippet(t, complainant_name or "")))

    # ── Completeness ────────────────────────────────────────────────────────
    filled = {
        "firNumber":       bool(fir_no),
        "complainantName": bool(complainant_name),
        "accusedName":     bool(label_accused or extra_persons[1:]),
        "dateOfIncident":  bool(incident_date),
        "policeStation":   bool(police_station),
        "district":        bool(district),
        "offenceType":     bool(offences),
        "sections":        bool(sections),
    }
    completeness = round(sum(filled.values()) / len(filled) * 100, 1)
    missing_core = [k for k, v in filled.items() if not v]

    # ── Pack fields for payload_generator ───────────────────────────────────
    fields = {
        "firNumber":          fir_no,
        "caseTitle":          _build_case_title(offences, label_accused or extra_persons[1:], address),
        "caseNumber":         fir_no,
        "dateOfIncident":     incident_date,
        "timeOfIncident":     incident_time,
        "policeStation":      police_station,
        "district":           district,
        "province":           province,
        "incidentAddress":    address,
        "complainantName":    complainant_name,
        "complainantFather":  complainant_father,
        "complainantCNIC":    complainant_cnic,
        "complainantAge":     complainant_age,
        "complainantPhone":   complainant_phone,
        "accusedPersons":     label_accused or (extra_persons[1:] if len(extra_persons) > 1 else []),
        "victims":            label_victims,
        "witnesses":          label_witnesses,
        "offenceType":        offences[0] if offences else None,
        "allOffences":        offences,
        "legalSections":      sections,
        "applicableAct":      act,
        "weaponsInvolved":    weapons,
        "vehiclesInvolved":   vehicles,
        "vehiclePlates":      plates,
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
        # The frontend's "extracted entity tiles" expect this exact shape:
        "extractedEntities": {
            "persons":   list(filter(None, [complainant_name] + (label_accused or []) + label_victims + label_witnesses + extra_persons)),
            "locations": list(filter(None, [address, police_station, district] + extra_locations)),
            "weapons":   weapons,
            "vehicles":  vehicles,
            "dates":     [d for d in [incident_date] if d],
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
    "firNumber", "complainantName", "accusedName", "dateOfIncident",
    "policeStation", "district", "offenceType", "sections",
)


def _build_case_title(offences: List[str], accused: List[str], location: Optional[str]) -> str:
    parts: List[str] = []
    if offences:
        parts.append(offences[0].title())
    if accused:
        parts.append(f"by {accused[0]}")
    if location:
        parts.append(f"at {location[:40]}")
    return " ".join(parts) if parts else "FIR Case"