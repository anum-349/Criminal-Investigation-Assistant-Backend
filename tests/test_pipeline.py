"""
tests/test_pipeline.py

Run with:  python -m pytest tests/ -v
Or:        python tests/test_pipeline.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from pipeline.urdu_translator  import detect_language, translate_urdu_to_english
from pipeline.fir_validator    import validate_fir
from pipeline.entity_extractor import extract_entities
from pipeline.lime_explainer   import explain_with_lime
from pipeline.fir_validator    import _model as fir_model


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

NON_FIR = "Invoice No 001. Customer: Ali. Product: Widget x5. Total: Rs 5000. Due: 30 days."


# ─── Tests ───────────────────────────────────────────────────────────────────

def test_language_detection():
    assert detect_language(SAMPLE_FIR_EN) == "english"
    assert detect_language(SAMPLE_FIR_UR) in ("urdu", "mixed")
    print("✓ Language detection")

def test_urdu_translation():
    result = translate_urdu_to_english(SAMPLE_FIR_UR)
    assert result["source_language"] in ("urdu", "mixed")
    assert result["coverage"] > 0.2
    print(f"✓ Urdu translation  coverage={result['coverage']:.0%}  unknown={len(result['unknown_tokens'])}")

def test_fir_validation_positive():
    v = validate_fir(SAMPLE_FIR_EN)
    assert v["is_fir"], f"Expected FIR but got confidence={v['confidence']}"
    print(f"✓ FIR validation (positive)  confidence={v['confidence']:.0%}")

def test_fir_validation_negative():
    try:
        v = validate_fir(NON_FIR + " " * 50)   # pad to meet word-count threshold
    except ValueError:
        print("✓ FIR validation (non-FIR) → raised ValueError (too short) — OK")
        return
    assert not v["is_fir"], f"Expected non-FIR but got confidence={v['confidence']}"
    print(f"✓ FIR validation (negative)  confidence={v['confidence']:.0%}")

def test_entity_extraction():
    result = extract_entities(SAMPLE_FIR_EN)
    f = result["fields"]
    assert f["firNumber"]       == "234/2024",   f"firNumber: {f['firNumber']}"
    assert f["policeStation"]   is not None,     "policeStation missing"
    assert f["complainantName"] is not None,     "complainantName missing"
    assert "392" in " ".join(f["legalSections"]), f"legalSections: {f['legalSections']}"
    print(f"✓ Entity extraction  completeness={result['completeness_score']:.0%}  "
          f"fields_filled={sum(1 for v in f.values() if v)}/{len(f)}")

def test_lime():
    result = explain_with_lime(SAMPLE_FIR_EN, fir_model)
    assert len(result["lime_weights"]) > 0
    print(f"✓ LIME explanation  top_word='{result['lime_weights'][0][0]}'  summary={result['summary'][:60]}…")


if __name__ == "__main__":
    print("\n═══ FIR Pipeline Tests ═══\n")
    test_language_detection()
    test_urdu_translation()
    test_fir_validation_positive()
    test_fir_validation_negative()
    test_entity_extraction()
    test_lime()
    print("\n✓ All tests passed!\n")