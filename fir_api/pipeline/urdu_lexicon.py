"""
pipeline/urdu_lexicon.py
────────────────────────
FIR-domain Urdu→English lexicon. **Fallback only.**

In the new pipeline this is *not* the primary translator — MarianMT
(Helsinki-NLP/opus-mt-ur-en) handles real translation. We keep this lexicon as:
  1. A degradation path if MarianMT weights fail to load
  2. A glossary the entity extractor can consult to normalise Urdu field
     labels ("تھانہ" → "police station") even when the surrounding text was
     translated by Marian — this avoids losing structured signals.

Keep it short and domain-specific. Adding generic Urdu vocab here would just
duplicate what Marian already does better.
"""

import re

# Urdu / Arabic / Persian / extended script ranges
URDU_SCRIPT_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")

URDU_LEXICON = {
    # ── Document / FIR terminology ─────────────────────────────────────────
    "مقدمہ":                "case",
    "ایف":                  "FIR",
    "ایف آئی آر":          "FIR",
    "رپورٹ":                "report",
    "درج":                  "registered",
    "شکایت":                "complaint",
    "شکایت کنندہ":         "complainant",
    "مدعی":                 "complainant",
    "مدعا":                 "accused",
    "مدعا علیہ":           "accused",
    "ملزم":                 "accused",
    "ملزمان":               "accused persons",
    "گواہ":                 "witness",
    "گواہان":               "witnesses",
    "مجرم":                 "criminal",
    "متاثرہ":               "victim",
    "متاثرین":              "victims",
    "مشتبہ":                "suspect",

    # ── Personal / identity ────────────────────────────────────────────────
    "نام":                  "name",
    "والد":                 "father",
    "ولدیت":                "son of",
    "عمر":                  "age",
    "سال":                  "years",
    "قومی شناختی کارڈ":   "CNIC",
    "شناختی کارڈ":         "ID card",
    "پتہ":                  "address",
    "رہائش":                "residence",
    "موبائل":               "mobile",
    "فون":                  "phone",

    # ── Location / time ────────────────────────────────────────────────────
    "تاریخ":                "date",
    "وقت":                  "time",
    "تھانہ":                "police station",
    "ضلع":                  "district",
    "صوبہ":                 "province",
    "شہر":                  "city",
    "گاؤں":                 "village",
    "محلہ":                 "locality",
    "مقام":                 "place",
    "مقام واقعہ":          "place of incident",
    "واقعہ":                "incident",

    # ── Offence types ──────────────────────────────────────────────────────
    "قتل":                  "murder",
    "قتل عمد":             "intentional murder",
    "چوری":                 "theft",
    "ڈکیتی":                "robbery",
    "لوٹ مار":              "looting",
    "دھوکہ دہی":           "fraud",
    "جعل سازی":            "forgery",
    "زنا بالجبر":          "rape",
    "زیادتی":               "assault",
    "اغوا":                 "kidnapping",
    "اغواء":                "kidnapping",
    "آتش زنی":              "arson",
    "توڑ پھوڑ":            "vandalism",
    "منشیات":               "narcotics",
    "ہتھیار":               "weapon",
    "ہتھیاروں":             "weapons",
    "اسلحہ":                "arms",
    "گولی":                 "bullet",
    "فائرنگ":               "firing",
    "چاقو":                 "knife",
    "پستول":                "pistol",
    "تشدد":                 "violence",
    "زخمی":                 "injured",
    "مار پیٹ":              "assault",
    "گرفتار":               "arrested",
    "گرفتاری":              "arrest",
    "فرار":                 "escaped",
    "روپوش":                "absconding",

    # ── Vehicle ────────────────────────────────────────────────────────────
    "گاڑی":                 "vehicle",
    "موٹر سائیکل":         "motorcycle",
    "کار":                  "car",
    "رجسٹریشن":             "registration",
    "نمبر پلیٹ":           "number plate",

    # ── Legal ──────────────────────────────────────────────────────────────
    "دفعہ":                 "section",
    "مجموعہ تعزیرات":     "Pakistan Penal Code",
    "ضابطہ فوجداری":       "Criminal Procedure Code",
    "قانون":                "law",
    "جرم":                  "offence",
    "سزا":                  "punishment",

    # ── Police ranks ───────────────────────────────────────────────────────
    "پولیس":                "police",
    "ایس ایچ او":          "SHO",
    "انسپکٹر":              "inspector",
    "کانسٹیبل":             "constable",
    "افسر":                 "officer",
    "تفتیش":                "investigation",
}