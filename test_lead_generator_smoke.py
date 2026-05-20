"""
tests/test_lead_generator_smoke.py
─────────────────────────────────────────────────────────────────────────────
Lightweight smoke tests that exercise each layer of the lead generator
in isolation. None of these need a running database; they use a
hand-built CaseContext to drive the prompt + validator + parser.

The only test that needs a network is `test_ollama_roundtrip`, which is
guarded by the OLLAMA_HOST env var — set it to skip if Ollama isn't
running locally.

Run with:
    pytest tests/test_lead_generator_smoke.py -v
"""
from __future__ import annotations

import os
import pytest

from services.lead_generator.context import (
    CaseContext, SuspectCtx, VictimCtx, EvidenceCtx,
    EntityCtx, LinkedCaseCtx, TimelineCtx,
    assess_context_depth,
)
from services.lead_generator.prompt import (
    SYSTEM_PROMPT, build_user_prompt, ALLOWED_LEAD_TYPES, ALLOWED_SEVERITIES,
)
from services.lead_generator.validate import (
    validate_lead, validate_batch, parse_llm_response,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def rich_context() -> CaseContext:
    """A case with enough data that the depth check passes and the
    validator has plenty of legitimate identifiers to recognise."""
    ctx = CaseContext(
        case_id          = "C-2053",
        case_internal_id = 42,
        case_title       = "Armed robbery at Tariq Road jeweller",
        case_type        = "Armed Robbery",
        case_status      = "Under Investigation",
        fir_number       = "145/2025",
        incident_date    = None,
        incident_time    = "14:30",
        description      = "Three armed men robbed the shop. CCTV partial.",
        motive           = "financial gain",
        modus            = "armed robbery during business hours",
        weapon_used      = "9mm pistol",
        ppc_sections     = "392 r/w 397",
        reporting_officer= "Insp. A. Khan",
        province         = "Sindh",
        city             = "Karachi",
        area             = "Saddar",
        suspects = [
            SuspectCtx(
                suspect_id="SUS-2053-01", name="Bilal Hussain",
                cnic="35201-1234567-1", age=34,
                relation="Primary suspect", criminal_record=True,
                affiliations="Anjuman gang", vehicle="white Toyota Corolla",
                reason="Identified by witness",
            ),
        ],
        evidence = [
            EvidenceCtx("EVD-2053-01", "Firearm rounds", "3 9mm cartridges recovered"),
            EvidenceCtx("EVD-2053-02", "CCTV footage", "partial from neighbouring shop"),
        ],
        entities = [
            EntityCtx("PERSON", "Rashid Khan, son of Latif", "FIR narrative, no record"),
            EntityCtx("VEHICLE", "LEB-7821 white", None),
        ],
        linked = [
            LinkedCaseCtx(
                case_id="C-2040", title="Armed robbery at PIA Colony jeweller",
                incident_date=None, similarity_score=0.87,
                link_type="SAME_SUSPECT",
                explanation="Bilal Hussain appears in both cases; same weapon",
                investigator_name="Insp. M. Aslam",
            ),
        ],
    )
    # Build the validation indices (build_case_context does this from
    # the DB; here we mirror its behaviour manually).
    for s in ctx.suspects:
        ctx.all_suspect_ids.add(s.suspect_id)
        if s.cnic:
            ctx.all_cnics_seen.add(s.cnic)
    for e in ctx.evidence:
        ctx.all_evidence_ids.add(e.evidence_id)
    for l in ctx.linked:
        ctx.linked_case_ids.add(l.case_id)
        ctx.all_case_ids_seen.add(l.case_id)
    ctx.all_case_ids_seen.add(ctx.case_id)
    return ctx


@pytest.fixture
def thin_context() -> CaseContext:
    """A near-empty case — depth check should reject it."""
    return CaseContext(
        case_id          = "C-9000",
        case_internal_id = 1,
        case_title       = "Unspecified incident",
        case_type        = None, case_status=None, fir_number=None,
        incident_date=None, incident_time=None,
        description=None, motive=None, modus=None,
        weapon_used=None, ppc_sections=None, reporting_officer=None,
        province=None, city=None, area=None,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Context depth
# ─────────────────────────────────────────────────────────────────────────────

def test_thin_context_fails_depth_check(thin_context):
    rep = assess_context_depth(thin_context)
    assert not rep.is_deep_enough
    assert rep.score < 3


def test_rich_context_passes_depth_check(rich_context):
    rep = assess_context_depth(rich_context)
    assert rep.is_deep_enough
    assert rep.score >= 3


# ─────────────────────────────────────────────────────────────────────────────
# Prompt builder
# ─────────────────────────────────────────────────────────────────────────────

def test_user_prompt_contains_all_sections(rich_context):
    p = build_user_prompt(rich_context)
    for marker in [
        "CASE C-2053",
        "SUSPECTS ON RECORD",
        "VICTIMS",
        "WITNESSES",
        "EVIDENCE ON FILE",
        "ENTITIES EXTRACTED FROM FIR",
        "LINKED CASES",
        "Generate 3-5 actionable leads",
    ]:
        assert marker in p, f"Missing section marker: {marker}"


def test_user_prompt_includes_identifiers(rich_context):
    p = build_user_prompt(rich_context)
    # Every identifier the LLM is allowed to cite must appear verbatim
    # in the prompt; otherwise the validator can't have built its
    # known-identifiers set from it either.
    assert "SUS-2053-01" in p
    assert "35201-1234567-1" in p
    assert "EVD-2053-01" in p
    assert "C-2040" in p


def test_system_prompt_lists_allowed_types():
    for t in ALLOWED_LEAD_TYPES:
        assert t in SYSTEM_PROMPT
    for s in ALLOWED_SEVERITIES:
        assert s in SYSTEM_PROMPT


# ─────────────────────────────────────────────────────────────────────────────
# Parser
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_clean_response():
    txt = '{"leads": [{"type": "Suspect Lead", "description": "x"}]}'
    out = parse_llm_response(txt)
    assert out["leads"][0]["type"] == "Suspect Lead"


def test_parse_strips_code_fence():
    txt = '```json\n{"leads": []}\n```'
    out = parse_llm_response(txt)
    assert out["leads"] == []


def test_parse_recovers_from_preamble():
    txt = 'Here are the leads:\n{"leads": [{"type": "Forensic"}]}'
    out = parse_llm_response(txt)
    assert out["leads"][0]["type"] == "Forensic"


def test_parse_normalises_bare_array():
    txt = '[{"type": "Evidence"}]'
    out = parse_llm_response(txt)
    assert out["leads"][0]["type"] == "Evidence"


def test_parse_normalises_single_lead():
    txt = '{"type": "Evidence", "description": "X"}'
    out = parse_llm_response(txt)
    assert len(out["leads"]) == 1


def test_parse_raises_on_garbage():
    with pytest.raises(ValueError):
        parse_llm_response("absolute nonsense, no json here")


# ─────────────────────────────────────────────────────────────────────────────
# Validator — these are the most important tests in this file. Each
# one corresponds to a failure mode the LLM is known to exhibit.
# ─────────────────────────────────────────────────────────────────────────────

def _valid_lead():
    """A minimal lead that satisfies every check. Tests mutate copies
    to introduce specific failures."""
    return {
        "type":            "Suspect Lead",
        "severity":        "High",
        "description":     "Interview Bilal Hussain about the white Corolla.",
        "next_step":       "Schedule a custodial interview with SUS-2053-01.",
        "evidence_basis":  "Suspect identified by witness; vehicle matches FIR entity.",
        "cited_case_ids":   ["C-2053"],
        "cited_cnics":      ["35201-1234567-1"],
        "cited_suspect_ids":["SUS-2053-01"],
        "confidence":      80,
    }


def test_validator_accepts_clean_lead(rich_context):
    result = validate_lead(_valid_lead(), rich_context)
    assert result.passed, f"Should accept; issues: {result.issues}"


def test_validator_rejects_hallucinated_cnic(rich_context):
    lead = _valid_lead()
    lead["cited_cnics"] = ["99999-9999999-9"]
    result = validate_lead(lead, rich_context)
    assert not result.passed
    assert any("Hallucinated CNIC" in i for i in result.issues)


def test_validator_rejects_malformed_cnic(rich_context):
    lead = _valid_lead()
    lead["cited_cnics"] = ["12345"]
    result = validate_lead(lead, rich_context)
    assert not result.passed
    assert any("Malformed CNIC" in i for i in result.issues)


def test_validator_rejects_unlinked_case(rich_context):
    lead = _valid_lead()
    lead["cited_case_ids"] = ["C-9999"]
    result = validate_lead(lead, rich_context)
    assert not result.passed
    assert any("non-linked case" in i for i in result.issues)


def test_validator_accepts_focal_case_citation(rich_context):
    lead = _valid_lead()
    lead["cited_case_ids"] = ["C-2053"]   # the focal case itself
    result = validate_lead(lead, rich_context)
    assert result.passed


def test_validator_accepts_linked_case_citation(rich_context):
    lead = _valid_lead()
    lead["cited_case_ids"] = ["C-2040"]   # the linked case
    result = validate_lead(lead, rich_context)
    assert result.passed


def test_validator_rejects_hallucinated_suspect_id(rich_context):
    lead = _valid_lead()
    lead["cited_suspect_ids"] = ["SUS-9999-99"]
    result = validate_lead(lead, rich_context)
    assert not result.passed
    assert any("Hallucinated suspect_id" in i for i in result.issues)


def test_validator_rejects_unknown_type(rich_context):
    lead = _valid_lead()
    lead["type"] = "Magical Intuition"   # not in lkp_lead_types
    result = validate_lead(lead, rich_context)
    assert not result.passed
    assert any("Unknown lead.type" in i for i in result.issues)


def test_validator_rejects_out_of_range_confidence(rich_context):
    for bad in [-1, 101, "high", None]:
        lead = _valid_lead()
        lead["confidence"] = bad
        result = validate_lead(lead, rich_context)
        assert not result.passed, f"Should have rejected confidence={bad!r}"


def test_validator_rejects_missing_required_fields(rich_context):
    for missing in ["type", "description", "next_step", "evidence_basis"]:
        lead = _valid_lead()
        lead[missing] = ""
        result = validate_lead(lead, rich_context)
        assert not result.passed, f"Should have rejected missing {missing}"


def test_validator_rejects_too_short_description(rich_context):
    lead = _valid_lead()
    lead["description"] = "Look"   # too short to be actionable
    result = validate_lead(lead, rich_context)
    assert not result.passed


def test_validator_truncates_oversized_description(rich_context):
    lead = _valid_lead()
    lead["description"] = "X" * 1500
    result = validate_lead(lead, rich_context)
    # Should still pass — long descriptions get truncated, not dropped.
    assert result.passed
    assert len(result.lead["description"]) <= 801


def test_batch_validator_separates_good_from_bad(rich_context):
    leads = [
        _valid_lead(),                                   # accept
        {**_valid_lead(), "cited_cnics": ["9-9-9"]},    # reject (malformed)
        {**_valid_lead(), "type": "Made up type"},      # reject (unknown type)
    ]
    report = validate_batch(leads, rich_context)
    assert len(report.accepted) == 1
    assert len(report.rejected) == 2


# ─────────────────────────────────────────────────────────────────────────────
# Ollama round-trip — only runs if Ollama is reachable.
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(
    not os.getenv("LEAD_GEN_OLLAMA_LIVE", "").lower() == "true",
    reason="Set LEAD_GEN_OLLAMA_LIVE=true to run the live model test",
)
def test_ollama_roundtrip():
    from services import llm_client
    assert llm_client.is_available(), "Ollama must be running for this test"
    resp = llm_client.generate(
        system_prompt='Output JSON only.',
        user_prompt='Return {"ok": true}.',
    )
    parsed = parse_llm_response(resp.text)
    assert parsed.get("ok") is True or "leads" in parsed