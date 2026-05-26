from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Dict

from dotenv import load_dotenv
import fitz                          # PyMuPDF
import easyocr
from groq import Groq

from fir_api.pipeline.fir_validator     import validate_fir, check_missing_fields
from fir_api.pipeline.entity_extractor  import extract_entities
from fir_api.pipeline.payload_generator import generate_payload
from fir_api.pipeline.lime_explainer    import explain_with_lime
from fir_api.pipeline.model_loader      import models

logger = logging.getLogger("fir.orchestrator")

# ─────────────────────────────────────────────────────────────────────────────
# OCR + Groq config
# ─────────────────────────────────────────────────────────────────────────────

load_dotenv()
GROQ_FIR_API_KEY = os.getenv("GROQ_FIR_API_KEY", "")
GROQ_FIR_MODEL   = os.getenv("GROQ_FIR_MODEL",   "llama-3.3-70b-versatile")

_groq_client: Groq | None = None
_ocr_reader:  easyocr.Reader | None = None


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
    """Lazy-load EasyOCR once per process (expensive cold-start)."""
    global _ocr_reader
    if _ocr_reader is None:
        logger.info("Loading EasyOCR (ur + en) …")
        _ocr_reader = easyocr.Reader(["ur", "en"], gpu=False, verbose=False)
        logger.info("✓ EasyOCR ready")
    return _ocr_reader


# ─────────────────────────────────────────────────────────────────────────────
# Urdu digit normaliser
# ─────────────────────────────────────────────────────────────────────────────

_URDU_TO_EN = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def _norm(text: str) -> str:
    return text.translate(_URDU_TO_EN)


# ─────────────────────────────────────────────────────────────────────────────
# Language detection
# ─────────────────────────────────────────────────────────────────────────────

# Simple heuristic: count Arabic-script characters vs. Latin characters.
# A real-world mixed FIR often has both; we call it "urdu" if >15% of
# non-whitespace chars are Arabic-script, "english" otherwise.
_ARABIC_BLOCK = re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")


def _detect_language(text: str) -> str:
    """Return 'urdu', 'english', or 'mixed'."""
    non_ws = [c for c in text if not c.isspace()]
    if not non_ws:
        return "unknown"
    arabic_ratio = sum(1 for c in non_ws if _ARABIC_BLOCK.match(c)) / len(non_ws)
    if arabic_ratio > 0.40:
        return "urdu"
    if arabic_ratio > 0.10:
        return "mixed"
    return "english"


# ─────────────────────────────────────────────────────────────────────────────
# Valid PPC + PECA sections
# ─────────────────────────────────────────────────────────────────────────────

# FIX: added PECA 2016 sections (3-23 are valid) and preserved original PPC set.
_VALID_PPC = {
    str(n) for n in
    list(range(109, 175)) + list(range(177, 220)) + list(range(267, 545)) +
    [147, 148, 149, 302, 324, 325, 326, 327, 328, 329, 330]
}

_VALID_PECA = {str(n) for n in range(3, 24)}   # PECA 2016, sections 3–23

_VALID_SECTIONS = _VALID_PPC | _VALID_PECA

# Memo / despatch reference numbers that look like sections but aren't.
# The FIA dispatch reference No. FSD/351-357 must not be parsed as sections.
_MEMO_REF_PATTERN = re.compile(
    r"(?:no\.|incharge|ref|reference|fsd|ccrc|hq)[^0-9\n]*"
    r"(\d{2,4}(?:[\-\/]\d{2,4})+)",
    re.I,
)


def _filter_spurious_sections(sections: list[str], raw_text: str) -> list[str]:
    """Remove section numbers that actually appear inside memo/reference lines."""
    memo_numbers: set[str] = set()
    for m in _MEMO_REF_PATTERN.finditer(raw_text):
        for part in re.split(r"[\-\/]", m.group(1)):
            memo_numbers.add(part.strip())
    return [s for s in sections if s not in memo_numbers]


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — OCR
# ─────────────────────────────────────────────────────────────────────────────

def _ocr_file(file_path: str) -> str:
    """Extract raw text from a PDF or image using PyMuPDF + EasyOCR.

    FIX: For English/digital PDFs, try PyMuPDF direct text extraction first —
    it is faster and more accurate than OCR for machine-generated PDFs.
    Fall back to EasyOCR only when direct extraction yields too little text.
    """
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".pdf":
        # --- Try direct text extraction first (works for digital/born-PDF) ---
        doc = fitz.open(file_path)
        direct_pages = []
        for page in doc:
            direct_pages.append(page.get_text("text"))
        doc.close()

        direct_text = "\n\n".join(direct_pages).strip()
        # If we got meaningful text (>100 chars), skip OCR entirely
        if len(direct_text) > 100:
            logger.info(
                "Direct PDF text extraction succeeded (%d chars) — skipping EasyOCR.",
                len(direct_text),
            )
            return direct_text

        # Otherwise fall through to EasyOCR (scanned/image PDF)
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


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — Regex pre-pass  (fast, no LLM cost)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_regex(raw: str) -> dict:
    """FIX summary of changes vs. original:
    - Police station regex now reads the actual header line ("Police Station: X")
      instead of a hardcoded city allowlist.
    - District regex no longer picks up "& Session" from judge names.
    - Phone regex uses strict Pakistani mobile format (03xx-xxxxxxx) so IMEI
      numbers (14 digits) are never matched.
    - Sections are filtered through _filter_spurious_sections() to remove
      despatch/memo reference numbers.
    - PECA sections (3–23) added to _VALID_SECTIONS.
    """
    text = _norm(raw)
    found: dict = {}

    # ── FIR / case number ────────────────────────────────────────────────────
    fir_candidates = re.findall(r"\b(\d{2,4})[/\\](\d{4})\b", text)
    if fir_candidates:
        for num, year in fir_candidates:
            if int(num) > 31 and 2000 <= int(year) <= 2030:
                found["fir_number"] = f"{num}/{year}"
                break
        if "fir_number" not in found:
            found["fir_number"] = f"{fir_candidates[-1][0]}/{fir_candidates[-1][1]}"

    # ── Date ─────────────────────────────────────────────────────────────────
    date_hits = re.findall(r"\b(\d{2})[/-](\d{2})[/-](20\d{2})\b", text)
    if date_hits:
        d, m, y = date_hits[0]
        found["date_of_report"] = f"{d}/{m}/{y}"
        # Only use as incident date if a second distinct date exists
        if len(date_hits) > 1:
            d2, m2, y2 = date_hits[1]
            found["date_of_incident"] = f"{d2}/{m2}/{y2}"
        # Also accept "Year YYYY" as a partial incident year marker
    else:
        year_only = re.search(r"(?:year|Year)\s+(20\d{2})", text)
        if year_only:
            found["incident_year"] = year_only.group(1)

    # ── Time ─────────────────────────────────────────────────────────────────
    time_hits = re.findall(r"\b(\d{1,2}:\d{2})\s*(?:PM|AM|بجے|pm|am)?\b", text)
    for t in time_hits:
        h, mi = t.split(":")
        if 0 <= int(h) <= 23 and 0 <= int(mi) <= 59:
            found["time"] = t
            break

    # ── CNICs ────────────────────────────────────────────────────────────────
    cnics = re.findall(r"\b(\d{5}-\d{7}-\d)\b", text)
    if not cnics:
        raw13 = re.findall(r"\b(\d{13})\b", text)
        cnics = [f"{c[:5]}-{c[5:12]}-{c[12]}" for c in raw13]
    if cnics:
        found["cnics"] = list(set(cnics))

    # ── Phones — strict Pakistani mobile: 03xx-xxxxxxx (11 digits total) ────
    # FIX: old regex matched bare digit runs like IMEI numbers.
    phones = re.findall(r"\b(0[3][0-9]{9})\b", re.sub(r"[\s\-]", "", text))
    if phones:
        found["contact_numbers"] = list(set(phones))

    # ── Legal sections ───────────────────────────────────────────────────────
    # FIX: extended to PECA sections; filtered against memo reference numbers.
    nums     = re.findall(r"\b(\d{1,4})\b", text)
    sections = sorted(set(n for n in nums if n in _VALID_SECTIONS))
    sections = _filter_spurious_sections(sections, text)
    if sections:
        found["sections"] = sections

    # ── Police station — read the actual field, not a city allowlist ─────────
    # FIX: the old code only matched Murree/Lahore/Rawalpindi/Islamabad.
    ps_match = re.search(
        r"(?:police\s+station|p\.?s\.?|thana|تھانہ)\s*[:\-]?\s*"
        r"([A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF\s\.]{2,60}?)"
        r"(?=[,\n\r]|circle|district|$)",
        text,
        re.I,
    )
    if ps_match:
        found["police_station"] = ps_match.group(1).strip()

    # ── Circle / Sub-circle ──────────────────────────────────────────────────
    circle_match = re.search(
        r"circle[/\s]*sub[\-\s]?circle\s*[:\-]?\s*"
        r"([A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF\s,\.]{2,60}?)"
        r"(?=[,\n\r]|$)",
        text,
        re.I,
    )
    if circle_match:
        found["circle"] = circle_match.group(1).strip()

    # ── District ─────────────────────────────────────────────────────────────
    # FIX: old regex caught "& Session" from "Additional District & Session Judge".
    # We now require the district name to consist only of word characters (no &).
    dist_match = re.search(
        r"(?:ضلع|district)\s*[:\-]?\s*([A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF\s]{1,25}?)"
        r"(?=[,\n\r]|tehsil|province|$)",
        text,
        re.I,
    )
    if dist_match:
        candidate = dist_match.group(1).strip()
        # Reject if it contains typical noise tokens
        if not re.search(r"&|session|judge|court|additional", candidate, re.I):
            found["district"] = candidate

    # ── Fallback: extract district from Circle/Sub-circle line (e.g. "CCRC, FAISALABAD") ──
    if "district" not in found and "circle" in found:
        # Typical format: "CCRC, FAISALABAD"
        circle_val = found["circle"]
        parts = [p.strip() for p in circle_val.split(",")]
        if len(parts) > 1:
            found["district"] = parts[-1].title()

    return found


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 — Groq/Llama extraction  (language-aware)
# ─────────────────────────────────────────────────────────────────────────────

def _llama_extract(raw: str, regex: dict, language: str = "urdu") -> dict:
    """FIX: Prompt now adapts to the detected document language.

    For English FIRs the model no longer receives an Urdu-script expert persona
    which confused it on clean English text.  For mixed/Urdu documents the
    original expert persona is preserved.
    """
    logger.info("[Groq] Calling %s (lang=%s) …", GROQ_FIR_MODEL, language)

    # ── System persona ───────────────────────────────────────────────────────
    if language == "english":
        system_msg = (
            "You are a Pakistani legal expert and document analyst. "
            "Extract information accurately from English-language FIR (First Information Report) text. "
            "Return only valid JSON."
        )
    else:
        system_msg = (
            "You are a Pakistani legal expert. "
            "Extract information accurately from noisy OCR Urdu FIR text. "
            "Return only valid JSON."
        )

    # ── User prompt ──────────────────────────────────────────────────────────
    if language == "english":
        instructions = """You are an expert at reading Pakistani FIR documents written in English.

The text below is from a scanned FIR — it may have minor OCR noise.
Extract every field precisely. Do not invent values; use null when a field is absent.

ALREADY CONFIRMED FIELDS (do not change these):
{regex_json}

KEY RULES FOR ENGLISH FIRs:
- The complainant is labeled "Name and residence of informant and complaint" or "Complainant:"
- The accused (defendant) is labeled "accused person" or named directly in the narrative
- Legal sections appear as "U/S X, Y, Z PECA 2016 R/W A, B PPC" — extract ALL section numbers
  separately (e.g. 3, 4, 5, 13, 14, 15, 16, 18, 19, 23 from PECA and 419, 420, 468, 471 from PPC)
- "R/W" means "read with" — capture the full section string
- The applicable act may be "PECA 2016" (Prevention of Electronic Crimes Act), "PPC", or both
- Investigating officer: look for "Investigation is entrusted to [Name]" or "steps taken" field
- Do NOT mistake memo/reference numbers (e.g. FSD/351-357) for legal section numbers
- Vehicle plate: extract the FULL plate (e.g. LEE-17-1316, not just LEE-17)
- Phone number: must be a Pakistani mobile (starting 03xx, 11 digits) — reject IMEI numbers

FIR TEXT:
{raw_text}"""
    else:
        instructions = """You are an expert at reading Pakistani FIR documents written in Urdu Nastaliq script.

The text below is OCR output from a scanned FIR — it WILL have noise, merged words, and errors.
Your task: interpret it carefully using your knowledge of FIR structure and Pakistani names.

ALREADY CONFIRMED FIELDS (do not change these):
{regex_json}

KEY RULES:
- Pakistani names like "شہزاد ولد X" mean "Shahzad son of X" — ولد means "son of"
- "مسلح" means armed, "زخمی" means injured, "قتل" means killed
- Accused list is usually "ملزمان" section — extract ALL names
- Complainant is "مدعی" or "شاکی"

OCR TEXT (interpret carefully, names may be merged):
{raw_text}"""

    prompt = instructions.format(
        regex_json=json.dumps(regex, ensure_ascii=False, indent=2),
        raw_text=raw[:6000],
    )

    prompt += """

Return ONLY this JSON (no markdown, no explanation):
{
  "fir_number_confirmed":  "NNN/YYYY or null",
  "date_of_report":        "DD/MM/YYYY or null",
  "date_of_incident":      "DD/MM/YYYY or null  (may differ from report date)",
  "time_confirmed":        "HH:MM AM/PM or null",
  "complainant_name":      "full name of complainant / informant",
  "complainant_father":    "father/husband name if mentioned (S/O, D/O, W/O)",
  "complainant_cnic":      "CNIC in 5-7-1 format or null",
  "complainant_contact":   "Pakistani mobile number (03xx format) or null",
  "complainant_address":   "full address of complainant",
  "accused": [
    {"name": "clean name", "father": "father name if S/O mentioned", "cnic": "CNIC if present", "description": "role/description"}
  ],
  "victims": [
    {"name": "name", "age": "age if mentioned", "injury": "type of injury"}
  ],
  "witnesses": ["name1", "name2"],
  "police_station":         "police station or agency name",
  "circle":                 "circle/sub-circle if mentioned",
  "district":               "district name only (e.g. Faisalabad) — not court names",
  "province":               "province name or null",
  "location_of_incident":   "specific location where the offence occurred",
  "legal_sections":         ["list", "every", "section", "number", "as", "strings"],
  "applicable_act":         "PECA 2016 / PPC / both — or null",
  "section_read_with":      "full 'X r/w Y' string or null",
  "weapons_used":           ["list weapons"],
  "vehicles":               [{"type": "car/motorcycle", "make": "model", "plate": "full plate number"}],
  "investigating_officer":  "full name",
  "io_rank":                "rank e.g. Sub Inspector, Assistant Director",
  "crime_summary":          "2-3 sentence summary in English of what happened",
  "statement_english":      "Complete formal English translation/transcription of the entire FIR narrative."
}"""

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


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 — Merge Groq fields → entity_extractor field shape
# ─────────────────────────────────────────────────────────────────────────────

def _merge_to_fields(regex: dict, llama: dict) -> dict:
    """FIX summary vs. original:
    - Uses new snake_case Groq keys (date_of_report, date_of_incident, legal_sections …)
    - dateOfReport and dateOfIncident are now kept separate (original collapsed them)
    - applicableAct comes from Groq "applicable_act" (was hardcoded to "Pakistan Penal Code")
    - legalSections from Groq wins over regex (regex had spurious memo refs)
    - Vehicle plate now taken from structured Groq vehicles list (was only regex)
    - investigatingOfficer and ioRank now populated from Groq fields
    - offenceType is set to None (was incorrectly set to the crime_summary string)
    - accusedUnknown detection handles both Arabic "نامعلوم" and English "unknown"
    """

    def _pick(*keys, fallback=None):
        for k in keys:
            v = llama.get(k) or regex.get(k)
            if v and str(v).strip() and "not explicitly" not in str(v).lower():
                return str(v).strip() if isinstance(v, str) else v
        return fallback

    # ── Sections ─────────────────────────────────────────────────────────────
    # Groq returns a proper list; fall back to regex only if Groq found nothing.
    groq_sections = llama.get("legal_sections") or []
    # Normalize: ensure they are strings, strip whitespace
    groq_sections = [str(s).strip() for s in groq_sections if str(s).strip()]
    sections = groq_sections if groq_sections else (regex.get("sections") or [])

    # ── CNICs / phones from regex (still more reliable than LLM for format) ──
    cnics    = regex.get("cnics", [])
    contacts = regex.get("contact_numbers", [])

    # ── Accused ──────────────────────────────────────────────────────────────
    accused_raw = llama.get("accused") or []
    accused_names = []
    for a in accused_raw:
        if not a.get("name"):
            continue
        name = a["name"].strip()
        if a.get("father"):
            name = f"{name} S/O {a['father'].strip()}"
        accused_names.append(name)

    # ── Victims / Witnesses ──────────────────────────────────────────────────
    victims_raw  = llama.get("victims") or []
    victim_names = [v.get("name", "").strip() for v in victims_raw if v.get("name")]

    witnesses_raw = llama.get("witnesses") or []
    witness_names = [w.strip() for w in witnesses_raw if isinstance(w, str) and w.strip()]

    # ── Vehicles and plates ──────────────────────────────────────────────────
    vehicles_raw = llama.get("vehicles") or []
    vehicle_types  = [v.get("type", "").strip() for v in vehicles_raw if v.get("type")]
    vehicle_plates = [v.get("plate", "").strip() for v in vehicles_raw if v.get("plate")]

    # ── Applicable act ───────────────────────────────────────────────────────
    # FIX: was hardcoded to "Pakistan Penal Code" whenever sections existed.
    applicable_act = _pick("applicable_act")
    if not applicable_act and sections:
        # Heuristic: if any section is a small number it's likely PECA
        has_peca = any(s in _VALID_PECA for s in sections)
        has_ppc  = any(s in _VALID_PPC  for s in sections)
        if has_peca and has_ppc:
            applicable_act = "PECA 2016 R/W PPC"
        elif has_peca:
            applicable_act = "PECA 2016"
        elif has_ppc:
            applicable_act = "Pakistan Penal Code"

    return {
        "firNumber":           _pick("fir_number_confirmed", "fir_number"),
        "caseTitle":           None,                   # built by entity_extractor
        "caseNumber":          _pick("fir_number_confirmed", "fir_number"),

        # FIX: keep report date and incident date separate
        "dateOfReport":        _pick("date_of_report"),
        "dateOfIncident":      _pick("date_of_incident", "date_of_report"),
        "timeOfIncident":      _pick("time_confirmed", "time"),

        "policeStation":       _pick("police_station"),
        "circle":              _pick("circle"),
        "tehsil":              None,
        "district":            _pick("district"),
        "province":            _pick("province"),
        "beat":                None,
        "incidentAddress":     _pick("location_of_incident"),
        "distanceFromPS":      None,

        "complainantName":     _pick("complainant_name"),
        "complainantFather":   _pick("complainant_father"),
        "complainantCNIC":     cnics[0] if cnics else _pick("complainant_cnic"),
        "complainantAge":      None,
        "complainantPhone":    contacts[0] if contacts else _pick("complainant_contact"),
        "complainantCaste":    None,
        "complainantProfession": None,
        "complainantAddress":  _pick("complainant_address"),

        "accusedPersons":      accused_names,
        "accusedUnknown":      any(
            "نامعلوم" in n or "unknown" in n.lower() or "na maloom" in n.lower()
            for n in accused_names
        ) if accused_names else False,
        "victims":             victim_names,
        "witnesses":           witness_names,

        # FIX: offenceType should NOT be the crime_summary string.
        # Leave it None and let entity_extractor's RE_OFFENCE populate it.
        "offenceType":         None,
        "allOffences":         [],
        "legalSections":       sections,
        "applicableAct":       applicable_act,
        "sectionReadWith":     _pick("section_read_with"),

        "weaponsInvolved":     llama.get("weapons_used") or [],
        "vehiclesInvolved":    vehicle_types,
        "vehiclePlates":       vehicle_plates,
        "hospitalReference":   None,

        # FIX: populated from Groq (was always None before)
        "investigatingOfficer": _pick("investigating_officer"),
        "ioRank":               _pick("io_rank"),
        "beltNumber":           None,

        # Pass through for entity_extractor NER
        "_english_statement":  llama.get("statement_english", ""),
        "_crime_summary":      llama.get("crime_summary", ""),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def run_pipeline(file_path: str, original_filename: str = "", on_step: callable = None) -> Dict[str, Any]:
    t0      = time.perf_counter()
    timings: Dict[str, float] = {}

    def _tick(name: str, start: float) -> None:
        timings[name] = round((time.perf_counter() - start) * 1000, 1)

    def _emit(step: str, label: str):
        if on_step:
            on_step({"step": step, "label": label})

    # ── 1. OCR ───────────────────────────────────────────────────────────────
    s = time.perf_counter()
    _emit("ocr", "OCR / text extraction…")
    raw_text = _ocr_file(file_path)
    _tick("ocr", s)

    if not raw_text or len(raw_text.strip()) < 20:
        raise ValueError(
            "Could not extract readable text from the document. "
            "Ensure the scan is clear and upright."
        )

    # ── 1b. Language detection ───────────────────────────────────────────────
    language = _detect_language(raw_text)
    _emit("lang", f"Detected language: {language}")
    logger.info("[LANG] Detected language: %s", language)

    # ── 2. Regex pre-pass ────────────────────────────────────────────────────
    s = time.perf_counter()
    regex_fields = _extract_regex(raw_text) 
    _emit("regex", "Running regex extraction…")
    logger.info("[REGEX] %s", json.dumps(regex_fields, ensure_ascii=False))
    _tick("regex", s)

    # ── 3. Groq / Llama extraction (language-aware) ──────────────────────────
    s = time.perf_counter()
    llama_fields = _llama_extract(raw_text, regex_fields, language=language)
    _tick("groq", s)

    # ── 4. FIR validation ────────────────────────────────────────────────────
    s = time.perf_counter()
    english_text = llama_fields.get("statement_english", "") or raw_text
    combined_for_validation = f"{english_text}\n\n{raw_text}"
    validation = validate_fir(combined_for_validation)
    _tick("validation", s)

    if not validation["is_fir"]:
        raise ValueError(
            f"This document does not appear to be a FIR. {validation['reason']}"
        )

    # ── 5. Merge → field shape entity_extractor understands ─────────────────
    s = time.perf_counter()
    _emit("merge", "Merge to fields")
    merged_fields = _merge_to_fields(regex_fields, llama_fields)
    _tick("merge", s)

    # ── 6. Entity extraction ─────────────────────────────────────────────────
    # For English FIRs run entity_extractor on the raw text directly.
    # For Urdu/mixed docs run it on the English translation Groq produced.
    s = time.perf_counter()
    ner_input_text = raw_text if language == "english" else english_text
    entity_result = extract_entities(
        text=ner_input_text,
        original_text=raw_text,
    )
    _emit("entities", "Extracting entities with field patching…")
    _patch_entity_fields(entity_result["fields"], merged_fields)
    _tick("entities", s)

    # ── 7. LIME ──────────────────────────────────────────────────────────────
    s = time.perf_counter()
    try:
        lime_result = _lime_on_sbert_classifier(english_text)
    except Exception as e:
        logger.warning("LIME explanation failed: %s", e)
        lime_result = {"lime_weights": [], "summary": "LIME unavailable for this request."}
    _tick("lime", s)

    # ── 8. Payload generation ────────────────────────────────────────────────
    s = time.perf_counter()
    meta = {
        "source_language":      language,
        "translation_coverage": None,
        "unknown_tokens":       [],
        "ocr_method":           "pymupdf_direct" if language == "english" else "easyocr",
        "ocr_confidence":       None,
        "completeness_score":   entity_result["completeness_score"],
        "missing_core_fields":  entity_result["missing_core_fields"],
    }
    payload_envelope = generate_payload(entity_result["fields"], meta)
    _tick("payload", s)

    duration_ms = round((time.perf_counter() - t0) * 1000, 1)
    flat         = _flatten_payload(payload_envelope, entity_result, llama_fields, language)

    return {
        "success":     True,
        "error":       None,
        "duration_ms": duration_ms,
        "payload":     flat,
        "steps": {
            "ocr": {
                "method":     meta["ocr_method"],
                "char_count": len(raw_text),
                "language":   language,
            },
            "regex":  regex_fields,
            "groq": {
                "model":             GROQ_FIR_MODEL,
                "accused_count":     len(llama_fields.get("accused") or []),
                "victims_count":     len(llama_fields.get("victims") or []),
                "weapons_count":     len(llama_fields.get("weapons_used") or []),
                "statement_length":  len(english_text),
            },
            "validation":        validation,
            "lime":              lime_result,
            "entity_extraction": entity_result["xai_breakdown"],
        },
        "timings_ms":   timings,
        "models_ready": models.status,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _patch_entity_fields(ent_fields: dict, merged: dict) -> None:
    """FIX: Added new Groq keys (investigating_officer → investigatingOfficer,
    circle, section_read_with, applicable_act) to the ALWAYS_GROQ list so they
    are never silently dropped.  Also added 'offenceType' to GROQ_FIR_FALLBACK
    so Groq doesn't override a good regex hit with None.
    """
    ALWAYS_GROQ = (
        "firNumber", "caseNumber", "dateOfReport", "dateOfIncident",
        "timeOfIncident", "policeStation", "district",
        "complainantName", "complainantFather", "complainantAddress",
        "accusedPersons", "accusedUnknown", "victims", "witnesses",
        "legalSections", "applicableAct", "sectionReadWith",
        "weaponsInvolved", "vehiclesInvolved", "vehiclePlates",
        "investigatingOfficer", "ioRank",   # FIX: these were in GROQ_FIR_FALLBACK
    )
    GROQ_FIR_FALLBACK = (
        "tehsil", "province", "incidentAddress",
        "complainantCNIC",
        "complainantPhone",
        "complainantAge", "complainantCaste", "complainantProfession",
        "offenceType",   # FIX: was missing; Groq None was overwriting regex hit
        "beltNumber",
    )

    for key in ALWAYS_GROQ:
        val = merged.get(key)
        if isinstance(val, list):
            if val:
                ent_fields[key] = val
            elif not ent_fields.get(key):
                ent_fields[key] = []
        elif isinstance(val, bool):
            ent_fields[key] = val
        elif val:
            ent_fields[key] = val

    for key in GROQ_FIR_FALLBACK:
        val = merged.get(key)
        if val and not ent_fields.get(key):
            ent_fields[key] = val


def _flatten_payload(
    envelope: dict,
    entity_result: dict,
    llama: dict,
    language: str = "unknown",
) -> dict:
    p           = envelope.get("payload", {})
    meta        = envelope.get("meta", {})
    fir_info    = p.get("firInfo", {})
    incident    = p.get("incident", {})
    persons     = p.get("persons", {})
    evidence    = p.get("evidence", {})
    police      = p.get("police", {})
    complainant = persons.get("complainant", {})

    return {
        "firNumber":          fir_info.get("firNumber"),
        "caseNumber":         fir_info.get("firNumber"),
        "caseTitle":          fir_info.get("caseTitle"),

        "policeStation":      fir_info.get("policeStation"),
        "tehsil":             fir_info.get("tehsil"),
        "district":           fir_info.get("district"),
        "province":           fir_info.get("province"),
        "beat":               fir_info.get("beat"),

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

        "complainantName":      complainant.get("name"),
        "complainantFather":    complainant.get("father"),
        "complainantCNIC":      complainant.get("cnic"),
        "complainantAge":       complainant.get("age"),
        "complainantPhone":     complainant.get("phone"),
        "complainantCaste":     complainant.get("caste"),
        "complainantProfession": complainant.get("profession"),
        "complainantAddress":   complainant.get("address"),

        "accusedPersons":     persons.get("accusedPersons", []),
        "accusedUnknown":     persons.get("accusedUnknown", False),
        "victims":            persons.get("victims", []),
        "witnesses":          persons.get("witnesses", []),

        "weaponsInvolved":    evidence.get("weaponsInvolved", []),
        "vehiclesInvolved":   evidence.get("vehiclesInvolved", []),
        "vehiclePlates":      evidence.get("vehiclePlates", []),
        "hospitalReference":  evidence.get("hospitalReference"),

        "investigatingOfficer": police.get("investigatingOfficer"),
        "ioRank":               police.get("ioRank"),
        "beltNumber":           police.get("beltNumber"),

        "crimeSummary":       llama.get("crime_summary", ""),
        "statementEnglish":   llama.get("statement_english", ""),

        # Frontend form aliases
        "incidentDate":       incident.get("dateOfIncident"),
        "reportingDate":      incident.get("dateOfReport"),
        "incidentTime":       incident.get("timeOfIncident"),
        "description":        llama.get("crime_summary", ""),
        "ppcSections":        ", ".join(incident.get("legalSections", []) or []),
        "address":            incident.get("incidentAddress"),
        "city":               fir_info.get("district"),

        "completenessScore":  meta.get("completenessScore"),
        "confidence":         _overall_confidence(meta),
        "extractedEntities":  entity_result.get("extractedEntities", {}),
        "missingFields":      entity_result.get("missingFields", []),
        # FIX: firLanguage now reflects actual detected language
        "firLanguage":        language.title(),
        "firType":            "FIR",
    }


def _overall_confidence(meta: dict) -> float:
    completeness = (meta.get("completenessScore") or 0) / 100.0
    return round(completeness * 100, 1)


def _lime_on_sbert_classifier(text: str, num_samples: int = 80) -> dict:
    if models.sentence_embedder is None or models.fir_classifier is None:
        return {"lime_weights": [], "summary": "LIME unavailable: classifier not loaded."}

    class _Wrapper:
        def predict_proba(self, texts):
            emb = models.sentence_embedder.encode(list(texts), show_progress_bar=False)
            return models.fir_classifier.predict_proba(emb)

    return explain_with_lime(text, _Wrapper(), num_samples=num_samples)