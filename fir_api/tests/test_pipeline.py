import os
import sys

# Make pipeline/ importable when running as a script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('FIR_TRANSLATION_MODEL', './models/opus-mt-ur-en')
os.environ.setdefault('FIR_EMBEDDING_MODEL', './models/paraphrase-multilingual-MiniLM-L12-v2')
os.environ.setdefault('FIR_URDU_NER_MODEL', './models/uner-uner-mbert')

# Warm models once for all tests
from pipeline.model_loader import warmup
warmup()
from pipeline.language_detector import detect_language
from pipeline.urdu_translator   import translate_urdu_to_english
from pipeline.fir_validator     import validate_fir
from pipeline.entity_extractor  import extract_entities

# ─── Fixtures ────────────────────────────────────────────────────────────────

SAMPLE_FIR_EN = """
FIR Number: 234/2024
Police Station: Gulberg
District: Lahore   Province: Punjab

Complainant Name: Muhammad Usman
CNIC: 35201-1234567-9   Age: 34 years   Phone: 0300-1234567

Date of Incident: 15/03/2024   Time of Incident: 10:30 PM
Place of Incident: Near Main Market, Gulberg III, Lahore

Nature of Offence: Robbery at gunpoint
Section: 392 PPC, Section 34 PPC

Accused: Unknown persons (3 in number) armed with pistol fled on motorcycle.
Victim: Muhammad Usman (complainant himself)
Witness: Nasir Khan, resident of same locality

Vehicle: Motorcycle (black), Registration No: LHR-2345
Weapons: pistol
"""

SAMPLE_FIR_UR = """
ایف آئی آر نمبر: 456/2024
تھانہ: سدر   ضلع: کراچی   صوبہ: سندھ

مدعی: احمد علی   عمر: 42 سال   شناختی کارڈ: 42101-9876543-1
تاریخ: 20/04/2024   وقت: 8 بجے شام
مقام واقعہ: لیاری، کراچی

جرم: ڈکیتی   دفعہ: 392 مجموعہ تعزیرات
ملزم: نامعلوم افراد   گواہ: خالد محمود
ہتھیار: پستول   گاڑی: موٹر سائیکل
"""

NON_FIR = (
    "Invoice No 001. Customer: Ali. Product: Widget x5. Total: Rs 5000. "
    "Due: 30 days. Payment terms: net 30. GST 17% applied. Thank you for "
    "your business — we appreciate the order and look forward to serving "
    "you again soon."
)


# ─── Tests ───────────────────────────────────────────────────────────────────

def test_language_detection():
    en = detect_language(SAMPLE_FIR_EN)
    ur = detect_language(SAMPLE_FIR_UR)
    assert en.language == "english", f"Expected english, got {en.language}"
    assert ur.language in ("urdu", "mixed"), f"Expected urdu/mixed, got {ur.language}"
    print(f"✓ Language detection  EN={en.language}({en.confidence:.2f})  "
          f"UR={ur.language}({ur.confidence:.2f})")


def test_urdu_translation():
    result = translate_urdu_to_english(SAMPLE_FIR_UR)
    assert result["source_language"] in ("urdu", "mixed")
    # Either MarianMT or lexicon fallback should produce *some* English output
    assert any(c.isascii() and c.isalpha() for c in result["translated_text"])
    print(f"✓ Urdu translation  method={result['method']}  "
          f"coverage={result['coverage']:.0%}")


def test_fir_validation_positive():
    v = validate_fir(SAMPLE_FIR_EN)
    assert v["is_fir"], (
        f"Expected FIR=True but got is_fir={v['is_fir']}, "
        f"confidence={v['confidence']}, reason={v['reason']}"
    )
    print(f"✓ FIR validation (positive, EN)  "
          f"confidence={v['confidence']:.0%}  method={v['method']}")


def test_fir_validation_urdu():
    # Mix translated + original (matches what the orchestrator passes)
    tr = translate_urdu_to_english(SAMPLE_FIR_UR)
    combined = tr["translated_text"] + "\n\n" + SAMPLE_FIR_UR
    v = validate_fir(combined)
    assert v["is_fir"], f"Urdu FIR mis-classified: {v}"
    print(f"✓ FIR validation (positive, UR)  confidence={v['confidence']:.0%}")


def test_fir_validation_negative():
    v = validate_fir(NON_FIR)
    assert not v["is_fir"], f"Expected non-FIR but got {v}"
    print(f"✓ FIR validation (negative)  "
          f"confidence={v['confidence']:.0%}  reason={v['reason'][:80]}…")


def test_entity_extraction_english():
    result = extract_entities(SAMPLE_FIR_EN)
    f = result["fields"]
    assert f["firNumber"] == "234/2024", f"firNumber: {f['firNumber']}"
    assert f["policeStation"], "policeStation missing"
    assert f["complainantName"], "complainantName missing"
    assert any("392" in s for s in f["legalSections"]), f"legalSections: {f['legalSections']}"
    assert "robbery" in (f["allOffences"] or []), f"offences: {f['allOffences']}"
    assert "pistol" in (f["weaponsInvolved"] or []), f"weapons: {f['weaponsInvolved']}"
    print(f"✓ Entity extraction (EN)  "
          f"completeness={result['completeness_score']:.0f}%  "
          f"fields_filled={sum(1 for v in f.values() if v)}/{len(f)}")


def test_entity_extraction_with_urdu_original():
    """If we pass the original Urdu text, the Urdu NER backend should fire
    (when loaded) and contribute extra persons/locations."""
    tr = translate_urdu_to_english(SAMPLE_FIR_UR)
    result = extract_entities(text=tr["translated_text"], original_text=SAMPLE_FIR_UR)
    f = result["fields"]
    # Even if Urdu NER isn't installed, regex should still pull the section number
    assert any("392" in s for s in f.get("legalSections", [])), f.get("legalSections")
    backends = result["xai_breakdown"]["ner_backends"]
    print(f"✓ Entity extraction (UR)  "
          f"completeness={result['completeness_score']:.0f}%  "
          f"backends={backends}")


# ─── CLI runner ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n═══ FIR Pipeline Tests ═══\n")
    test_language_detection()
    test_urdu_translation()
    test_fir_validation_positive()
    test_fir_validation_urdu()
    test_fir_validation_negative()
    test_entity_extraction_english()
    test_entity_extraction_with_urdu_original()
    print("\n✓ All tests passed!\n")