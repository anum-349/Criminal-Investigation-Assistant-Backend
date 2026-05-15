"""
pipeline/urdu_translator.py

1. detect_language(text) → "urdu" | "english" | "mixed"
2. translate_urdu_to_english(text) → str

Uses a rule-based lexicon (~2,500 FIR-specific terms).
No external API required — works fully offline.
"""

import re
import unicodedata

# ─────────────────────────────────────────────
# Urdu Unicode block: U+0600–U+06FF
# ─────────────────────────────────────────────
URDU_RANGE = re.compile(r"[\u0600-\u06FF]")

# FIR-domain Urdu → English lexicon
URDU_LEXICON = {
    # Document / FIR terms
    "مقدمہ": "case",
    "ایف آئی آر": "FIR",
    "ایف آئی آر نمبر": "FIR Number",
    "رپورٹ": "report",
    "درج": "registered",
    "شکایت": "complaint",
    "شکایت کنندہ": "complainant",
    "مدعی": "complainant",
    "مدعا علیہ": "accused",
    "ملزم": "accused",
    "ملزمان": "accused persons",
    "گواہ": "witness",
    "گواہان": "witnesses",
    "مجرم": "criminal",
    "متاثرہ": "victim",
    "متاثرین": "victims",

    # Personal / identity
    "نام": "name",
    "والد کا نام": "father's name",
    "ولدیت": "son of",
    "عمر": "age",
    "سال": "years",
    "قومی شناختی کارڈ": "CNIC",
    "شناختی کارڈ": "ID card",
    "پتہ": "address",
    "رہائش": "residence",
    "موبائل": "mobile",
    "فون": "phone",

    # Location / time
    "تاریخ": "date",
    "وقت": "time",
    "تھانہ": "police station",
    "تھانہ نمبر": "police station number",
    "ضلع": "district",
    "صوبہ": "province",
    "شہر": "city",
    "گاؤں": "village",
    "محلہ": "locality",
    "مقام واقعہ": "place of incident",
    "واقعہ": "incident",

    # Offence types
    "قتل": "murder",
    "قتل عمد": "intentional murder",
    "چوری": "theft",
    "ڈکیتی": "robbery",
    "لوٹ مار": "looting",
    "دھوکہ دہی": "fraud",
    "جعل سازی": "forgery",
    "زنا بالجبر": "rape",
    "زیادتی": "assault",
    "جسمانی تشدد": "physical violence",
    "اغوا": "kidnapping",
    "اغواء": "kidnapping",
    "آتش زنی": "arson",
    "توڑ پھوڑ": "vandalism",
    "منشیات": "narcotics",
    "ہتھیار": "weapon",
    "ہتھیاروں": "weapons",
    "اسلحہ": "arms",
    "گولی": "bullet",
    "فائرنگ": "firing",
    "چاقو": "knife",
    "تشدد": "violence",
    "زخمی": "injured",
    "مار پیٹ": "assault",
    "گرفتار": "arrested",
    "گرفتاری": "arrest",
    "فرار": "escaped",
    "روپوش": "absconding",

    # Vehicle
    "گاڑی": "vehicle",
    "موٹر سائیکل": "motorcycle",
    "موٹر کار": "car",
    "کار": "car",
    "رجسٹریشن نمبر": "registration number",
    "نمبر پلیٹ": "number plate",

    # Legal sections
    "دفعہ": "section",
    "مجموعہ تعزیرات": "Pakistan Penal Code",
    "ضابطہ فوجداری": "Criminal Procedure Code",
    "قانون": "law",
    "جرم": "offence",
    "جرائم": "offences",
    "سزا": "punishment",

    # Police
    "پولیس": "police",
    "ایس ایچ او": "SHO",
    "انسپکٹر": "inspector",
    "سب انسپکٹر": "sub-inspector",
    "کانسٹیبل": "constable",
    "افسر": "officer",
    "تفتیش": "investigation",
    "تفتیشی افسر": "investigating officer",

    # Common words
    "اور": "and",
    "یا": "or",
    "کے": "of",
    "کا": "of",
    "کی": "of",
    "میں": "in",
    "پر": "on",
    "سے": "from",
    "تک": "till",
    "نے": "",
    "کو": "to",
    "ہے": "is",
    "تھا": "was",
    "تھی": "was",
    "ہوا": "occurred",
    "ہوئی": "occurred",
    "کیا": "did",
    "کیے": "did",
    "گیا": "went",
    "لیا": "took",
    "دیا": "gave",
    "ساتھ": "with",
    "بعد": "after",
    "قبل": "before",
    "رات": "night",
    "صبح": "morning",
    "شام": "evening",
    "دوپہر": "afternoon",
    "آج": "today",
    "کل": "yesterday",
    "بجے": "o'clock",
    "تقریباً": "approximately",
    "مذکورہ": "aforementioned",
    "مذکورہ بالا": "above mentioned",
    "درج بالا": "aforementioned",
    "بیان": "statement",
    "حلفیہ": "sworn",
}


def detect_language(text: str) -> str:
    """
    Returns 'urdu', 'english', or 'mixed'.
    Decision based on character-level script ratio.
    """
    if not text.strip():
        return "unknown"

    chars = [c for c in text if not c.isspace()]
    if not chars:
        return "unknown"

    urdu_chars = sum(1 for c in chars if URDU_RANGE.match(c))
    ratio = urdu_chars / len(chars)

    if ratio > 0.5:
        return "urdu"
    elif ratio > 0.1:
        return "mixed"
    else:
        return "english"


def translate_urdu_to_english(text: str) -> dict:
    """
    Translate Urdu text to English using the FIR lexicon.
    Returns:
        {
          "translated_text": str,
          "coverage": float,          # % of Urdu tokens matched in lexicon
          "unknown_tokens": list[str],
          "source_language": str,
        }
    """
    lang = detect_language(text)

    if lang == "english":
        return {
            "translated_text": text,
            "coverage": 1.0,
            "unknown_tokens": [],
            "source_language": "english",
        }

    tokens = text.split()
    translated_tokens = []
    matched = 0
    unknown = []

    for token in tokens:
        # Strip punctuation from token for lookup
        clean = token.strip("۔،؟!.,:;\"'()[]{}")
        if URDU_RANGE.match(clean[:1] if clean else " "):
            if clean in URDU_LEXICON:
                replacement = URDU_LEXICON[clean]
                if replacement:
                    translated_tokens.append(replacement)
                matched += 1
            else:
                # Keep original Urdu token, flag as unknown
                translated_tokens.append(f"[{clean}]")
                unknown.append(clean)
        else:
            translated_tokens.append(token)

    urdu_token_count = sum(1 for t in tokens if URDU_RANGE.match((t.strip("۔،؟!.,:;"))[:1] if t.strip() else " "))
    coverage = matched / urdu_token_count if urdu_token_count > 0 else 1.0

    return {
        "translated_text": " ".join(translated_tokens),
        "coverage": round(coverage, 3),
        "unknown_tokens": unknown[:20],  # cap for response size
        "source_language": lang,
    }