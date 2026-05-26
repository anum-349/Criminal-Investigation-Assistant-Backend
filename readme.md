create .env with secrets 
JWT_SECRET = "cia-2026"
SQLALCHEMY_DATABASE_URL = "postgresql://postgres:password@localhost:5432/database_name" 
ADMIN_SECRET_CODE = "admin-secret-2026"
MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15
LOG_LEVEL = "INFO"
CORS_ORIGINS = http://localhost:5173,http://localhost:3000


run in cmd:
mkdir sqlite_datas
python models.py
python seeds.py
python seed_users.py
python seed_cases.py



# FIR Processing Pipeline

Accepts a FIR document (PDF or image, **Urdu or English**) → extracts text → validates it's a FIR → extracts all fields → returns a structured JSON payload for form auto-fill.

**No external AI APIs.** All processing is local.

---

## Architecture

```
Upload (PDF / JPG / PNG)
  │
  ▼
text_extractor.py        pdfplumber (text PDFs) or Tesseract OCR (scans/images)
  │
  ▼
urdu_translator.py       Detects Urdu (Unicode U+0600–U+06FF), translates via 2,500-term lexicon
  │
  ▼
fir_validator.py         TF-IDF + Logistic Regression classifies "is this a FIR?"
  │                      → if NO → raise ValueError → frontend shows toast error
  ▼
lime_explainer.py        LIME perturbation explains WHICH words drove the FIR classification
  │
  ▼
entity_extractor.py      20+ regex patterns extract every form field (XAI-tagged)
  │
  ▼
payload_generator.py     Structures extracted data into registration form payload
  │
  ▼
api/main.py              FastAPI POST /api/process-fir → JSON response
```

---

## Quickstart — Docker (recommended, works on Windows/Mac/Linux)

```bash
# 1. Clone / copy this folder
# 2. Build image (Ubuntu + Tesseract + Urdu pack inside)
docker build -t fir-pipeline .

# 3. Start API
docker run -p 8000:8000 fir-pipeline

# Or with docker-compose (supports hot-reload)
docker-compose up
```

API is now at **http://localhost:8000**

---

## Quickstart — Local (Ubuntu/WSL)

```bash
# System dependencies
sudo apt-get install tesseract-ocr tesseract-ocr-urd tesseract-ocr-eng poppler-utils

# Python dependencies
pip install -r requirements.txt

# Run
uvicorn api.main:app --reload --port 8000
```

---

## Quickstart — Windows (without Docker)

1. Download Tesseract installer from https://github.com/UB-Mannheim/tesseract/wiki
2. During install, check **Urdu** under "Additional language data"
3. Add Tesseract to your PATH (e.g. `C:\Program Files\Tesseract-OCR`)
4. `pip install -r requirements.txt`
5. `uvicorn api.main:app --reload --port 8000`

---

## API Usage

### `POST /api/process-fir`

```
Content-Type: multipart/form-data
Body: file=<your PDF or image>
```

**Success response (200)**

```json
{
  "status": "success",
  "payload": {
    "firInfo": {
      "firNumber": "234/2024",
      "policeStation": "Gulberg",
      "district": "Lahore",
      "province": "Punjab",
      "caseTitle": null
    },
    "incident": {
      "dateOfIncident": "15/03/2024",
      "timeOfIncident": "10:30 PM",
      "incidentAddress": "Near Main Market, Gulberg III, Lahore",
      "offenceType": "Robbery at gunpoint",
      "legalSections": ["392", "34"]
    },
    "persons": {
      "complainant": {
        "name": "Muhammad Usman",
        "cnic": "35201-1234567-9",
        "age": "34",
        "phone": "0300-1234567"
      },
      "accusedPersons": ["Unknown persons"],
      "victims": ["Muhammad Usman"],
      "witnesses": ["Nasir Khan"]
    },
    "evidence": {
      "weaponsInvolved": ["pistol"],
      "vehiclesInvolved": ["motorcycle"],
      "vehiclePlates": ["LHR-2345"]
    }
  },
  "meta": {
    "sourceLanguage": "english",
    "completenessScore": 1.0,
    "missingCoreFields": [],
    "ocrMethod": "pdfplumber",
    "ocrConfidence": 0.95
  },
  "xai": {
    "validation": {
      "is_fir": true,
      "confidence": 0.92,
      "reason": "Document classified as FIR with 92% confidence. 8 FIR-specific keywords detected.",
      "top_tfidf_features": [["fir", 0.43], ["complainant", 0.38], ...]
    },
    "lime": {
      "lime_weights": [["robbery", 0.12], ["complainant", 0.09], ...],
      "summary": "Words pushing toward FIR: robbery, complainant, police, section, accused."
    },
    "entity_extraction": {
      "firNumber": { "value": "234/2024", "matched": true, "explanation": "Looks for 'FIR No'..." },
      ...
    }
  }
}
```

**Error response (422)** — shown as toast in frontend

```json
{
  "detail": {
    "toast": "error",
    "message": "This document does not appear to be a FIR. Please upload a First Information Report."
  }
}
```

---

## XAI — Why these decisions?

Every response includes a full `xai` block:

| Section | What it explains |
|---------|-----------------|
| `xai.validation.top_tfidf_features` | Which TF-IDF terms most influenced the "is FIR" classification |
| `xai.lime.lime_weights` | Which words drove the probability up/down (LIME perturbation) |
| `xai.entity_extraction.<field>` | Which regex pattern extracted each field, whether it matched |
| `xai.translation` | Coverage %, which Urdu tokens had no lexicon match |
| `xai.ocr` | Which OCR method was used, confidence estimate |

---

## Why LIME over SHAP?

| | LIME | SHAP |
|--|------|------|
| Works with any model | ✓ | Only tree/linear natively |
| Text-friendly | ✓ | Needs adapter |
| Computationally cheap | ✓ (200 samples) | ✓ for linear |
| Accuracy | Local approximation | Exact (linear) |

For this pipeline's TF-IDF + LogReg setup, LIME is the natural fit — it explains decisions in terms of actual words from the document.

---

## Extending the Model

To improve FIR classification accuracy, add real FIR examples to `fir_validator.py`:

```python
FIR_SAMPLES = [
    "your real fir text here ...",
    ...
]
```

The model re-trains at startup. For production, save/load the trained model with `joblib`.

---

## Running Tests

```bash
python tests/test_pipeline.py
```


# AI Lead Generator (v2 — Comprehensive)

Llama 3.1 8B–powered next-step suggester for the AI-Powered Criminal Investigation Assistant. Generates 3–5 grounded leads per case, each citing real records — never invented ones.

## What v2 adds over v1

v1 worked off suspects, victims, witnesses, evidence, FIR entities, linked cases, and a simple timeline. v2 adds every aspect a real investigator would weigh:

| Aspect | Source field(s) | What the LLM does with it |
|---|---|---|
| **Forensic findings on victims** | `victim_forensic_findings.finding_text` | Cite by `FF-VIC-2053-01-01` style ID |
| **Spatial density** | Haversine over `Location.lat/lng` within 3 km × 30 d | "5 nearby cases, 3 same crime type — possible pattern crime" |
| **Statement contradictions** | `suspect.alibi` vs `witness.description` vs `victim.statement` | "Alibi says Gulberg at 14:30; witness saw vehicle at scene at 14:30" |
| **Suspects detained > 15 days** | `case_suspect.created_at` + status="Detained" | "Review remand authorisation" |
| **Suspects with criminal record, not arrested** | `case_suspect.criminal_record AND NOT arrested` | "Prior record + at-large = arrest justified" |
| **Victims marked deceased but no postmortem** | `case_victim.status="Deceased" AND postmortem=False` | "Schedule postmortem immediately" |
| **Victim follow-up overdue** | `case_victim.next_follow_up < today` | "Follow-up overdue by N days" |
| **Witnesses needing protection** | `case_witness.protection_required` | "Confirm protection assignment" |
| **Witnesses with low credibility** | `witness_credibility.label IN (Low, Contradicted, Unverified)` | "Find corroborating witness" |
| **Non-cooperating victims/witnesses** | `cooperative=False`/`cooperating=False` | "Refer to victim-support unit" |
| **CCTV flagged but no CCTV evidence** | `case.cctv_available=True` + no Evidence with cctv/video type | "Collect CCTV from neighbouring premises" |
| **Investigator hypotheses** | `investigation_notes` | LLM can extend/echo investigator's own notes |
| **Cross-case shared suspects** | `case_links` + matched CNICs | "Cross-reference interview logs from linked case" |
| **Evidence backlog** | `Evidence.sha256_hash=NULL` for ≥3 items | "Escalate backlog to forensics" |
| **Stale open cases** | No timeline event in 14+ days | "Reactivate dormant investigation" |
| **FIR-named persons/vehicles never recorded** | `extracted_entities.matched_person_id=NULL` | "Trace unmatched person Rashid Khan" |
| **Theft cases without recovery status** | `theft_details.recovery_status=NULL` | "Initiate recovery inquiry" |
| **Sexual assault completeness** | `sexual_assault_details.medical_exam/protection_order` | Time-sensitive evidence + protection order leads |
| **Critical priority with no suspects** | `priority="Critical" AND suspects=[]` | Highest-impact gap |
| **Completeness report** | `completeness_reports.score + missing_fields` | LLM avoids re-suggesting what the system already flagged |

## File layout

```
services/
  llm_client.py                            (275 lines)  Ollama wrapper
  lead_generator/
    __init__.py                            (406 lines)  Orchestrator
    context.py                             (1387 lines) DB → CaseContext + 18 gap codes
    prompt.py                              (581 lines)  14-section prompt builder
    validate.py                            (408 lines)  Validation gate, 8 citation types
routes/
  leads_ai_routes.py                       (220 lines)  3 endpoints (health, generate, stream)
src/
  services/leadAiApi.js                    (225 lines)  Frontend API helper, NDJSON stream
  components/GenerateLeadsButton.jsx       (233 lines)  Drop-in React button
  pages/CASELEADS_PATCH.md                              How to integrate
tests/
  test_lead_generator_smoke.py             (790 lines)  43 tests covering all logic
MAIN_PY_PATCH.py                                        Backend mount snippet
README.md                                               This file
```

## The 18 derived gap codes

`context.py` runs `_compute_gaps()` after building the bundle. Every gap maps to one or more lead types the system prompt allows. The LLM is told to lead with these.

| Gap code | Triggers when | Typical lead type |
|---|---|---|
| `CRITICAL_NO_SUSPECT` | priority ≥ High + zero suspects | Suspect Lead |
| `DECEASED_NO_POSTMORTEM` | Victim deceased + `postmortem=False` | Forensic |
| `DETAINED_BEYOND_LIMIT` | Suspect detained > 15 days | Procedural |
| `OVERDUE_FOLLOW_UP` | `victim.next_follow_up < today` | Procedural |
| `CCTV_FLAG_NO_EVIDENCE` | `case.cctv_available` + no CCTV evidence | Evidence |
| `WITNESS_FLAG_NO_RECORD` | `case.witness_available` + zero witnesses | Witness Statement |
| `RECORD_NOT_ARRESTED` | Suspect has criminal record + `arrested=False` | Suspect Lead |
| `SUSPECT_NO_ALIBI` | Suspect with no alibi recorded | Suspect Lead |
| `OPEN_NO_RECENT_ACTIVITY` | Open case + no timeline event in 14+ days | Procedural |
| `ENTITY_PERSON_UNMATCHED` | FIR mentions a person not in records | Suspect Lead |
| `ENTITY_VEHICLE_UNMATCHED` | FIR mentions a vehicle not in records | Evidence |
| `THEFT_NO_RECOVERY_STATUS` | Theft case + no recovery status | Procedural |
| `SA_NO_MEDICAL_EXAM` | Sexual assault case + no medical exam | Forensic |
| `SA_NO_PROTECTION_ORDER` | Sexual assault case + no protection order | Procedural |
| `LOW_CRED_AS_PRIMARY` | All witnesses low-credibility | Witness Statement |
| `WITNESS_PROTECTION_REQUIRED` | Witness needs protection | Procedural |
| `EVIDENCE_BACKLOG` | ≥3 evidence items pending analysis | Forensic |
| `VICTIM_NOT_COOPERATING` | `cooperative=False` | Procedural |
| `WITNESS_NOT_COOPERATING` | `cooperating=False` | Procedural |
| `CROSS_CASE_SHARED_SUSPECT` | Shared CNIC on linked case | Cross-reference |
| `SPATIAL_PATTERN` | ≥3 same-type cases within 3 km × 30 d | Location Pattern |

## What a generated lead looks like

For the test fixture (1 suspect detained 20 days w/o alibi, 1 deceased victim no postmortem, 1 low-cred witness, pending CCTV evidence, FIR mentions vehicle LEB-7821 never traced, 3 nearby same-type robberies):

```json
{
  "type": "Suspect Lead",
  "severity": "High",
  "description": "Suspect SUS-2053-01 (Bilal Hussain, CNIC 35201-1234567-1) has been detained 18 days and his alibi places him at Gulberg Sweets at 14:30, but witness WIT-2053-01 placed his vehicle (white Toyota Corolla) leaving the scene at the same time.",
  "next_step": "Re-interview SUS-2053-01 to address the timeline contradiction with WIT-2053-01's statement. Verify the Gulberg Sweets alibi via CCTV from that location.",
  "evidence_basis": "Suspect detained beyond standard remand window; alibi directly contradicts witness account; vehicle matches FIR entity LEB-7821.",
  "cited_suspect_ids": ["SUS-2053-01"],
  "cited_witness_ids": ["WIT-2053-01"],
  "cited_cnics": ["35201-1234567-1"],
  "cited_evidence_ids": ["EVD-2053-04"],
  "addresses_gap": "DETAINED_BEYOND_LIMIT",
  "confidence": 85
}
```

The lead's `description` and `next_step` reference the suspect/witness IDs verbatim — without those, the validator drops the lead as "generic". Provenance footer added on persist:

```
─── AI Lead Provenance ───
Evidence basis: Suspect detained beyond standard remand window; alibi directly contradicts witness account; vehicle matches FIR entity LEB-7821.
Cited suspects: SUS-2053-01 · CNICs: 35201-1234567-1
Cited witnesses: WIT-2053-01
Cited evidence: EVD-2053-04
Addresses gap: DETAINED_BEYOND_LIMIT
```

## How validation prevents hallucination

`validate.py` enforces this in order, dropping the lead at the first hard failure:

1. **Required fields present** (type, description, next_step, evidence_basis, confidence)
2. **Type ∈ allow-list** of 10 lead types
3. **Confidence ∈ [0, 100]**
4. **Every cited identifier matches format AND set membership** —
   - CNICs in `\d{5}-\d{7}-\d`
   - Case IDs in `C-\d+`
   - Suspect IDs in `(SUS|S|SUSP)-…`
   - Victim IDs in `(VIC|V)-…`
   - Witness IDs in `(WIT|W)-…`
   - Evidence IDs in `(EVD|E|EV)-…`
   - Finding IDs in `FF-…`
   - Note IDs in `NOTE-…`
   - Plus each must be in the corresponding pre-built `ctx.all_*_ids` set
5. **At least one citation exists** (anti-generic check)
6. **next_step contains an action verb** (interview/verify/recover/escalate/…)
7. **addresses_gap matches a real derived gap code** (soft — cleared if invalid)

Anything that fails is logged with `[lead-gen] Lead #N REJECTED: [issue list]` so the FYP defense has audit trail.

## Setup

### 1. Install Ollama and pull the model

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.1:8b-instruct-q4_K_M     # ~4.7 GB
ollama serve                                 # leave running
```

### 2. Backend wiring

Apply the two-line patch in `MAIN_PY_PATCH.py` to your existing `main.py`:

```python
from routes import leads_ai_routes
app.include_router(leads_ai_routes.router,
                   prefix="/api/investigator/cases",
                   tags=["AI Leads"])
```

Optional env vars (defaults in parens):

```
OLLAMA_HOST       (http://localhost:11434)
OLLAMA_MODEL      (llama3.1:8b-instruct-q4_K_M)
OLLAMA_TIMEOUT    (60)
```

### 3. Frontend wiring

Apply the two edits described in `src/pages/CASELEADS_PATCH.md` to your existing `CaseLeads.jsx`. Drop-in usage:

```jsx
import GenerateLeadsButton from "@/components/GenerateLeadsButton";

<GenerateLeadsButton
  caseId={caseId}
  onLead={(lead) => {
    setLeads(c => [lead, ...c]);
    setTotal(t => t + 1);
  }}
  onDone={() => fetchLeads()}
/>
```

## Endpoints

```
GET  /api/investigator/cases/leads/ai/health
POST /api/investigator/cases/{case_id}/leads/generate           (blocking)
POST /api/investigator/cases/{case_id}/leads/generate/stream    (NDJSON)
```

Stream event shape:

```
{"event":"started", "case_id":"C-2053", "model":"llama3.1:8b-instruct-q4_K_M"}
{"event":"context", "depth_score":11, "gap_count":14}
{"event":"lead",    "lead":{…LeadRow}}
{"event":"rejected","count":1, "issues":[…]}
{"event":"done",    "accepted":3, "rejected":1, "duration_ms":5430,
                    "raw_lead_count":4, "gap_count":14, "reason":null}
```

The frontend renders depth_score and gap_count live ("Context built · depth=11 · 14 gaps spotted → asking model…") so the investigator can see the system is working even while Llama is chewing through the 7 KB prompt.

## Testing

Standalone (no DB needed — uses MagicMock stubs):

```bash
cd backend
PYTHONPATH=. python tests/test_lead_generator_smoke.py
```

```
  ✓ test_thin_context_fails_depth_check
  ✓ test_rich_context_passes_depth_check
  ✓ test_prompt_includes_every_section
  ✓ test_prompt_surfaces_all_identifier_types
  ✓ test_prompt_renders_all_flag_markers
  ✓ test_prompt_lists_every_gap_code_in_fixture
  ✓ test_prompt_renders_statements_block
  ✓ test_prompt_renders_spatial_density
  ✓ test_system_prompt_lists_allowed_types_and_severities
  ✓ test_system_prompt_documents_every_citation_type
  ✓ test_parse_clean_response  (+ 5 more parser cases)
  ✓ test_validator_accepts_clean_lead
  ✓ test_validator_rejects_hallucinated_identifier  (8 param cases)
  ✓ test_validator_rejects_malformed_identifier     (8 param cases)
  ✓ test_validator_accepts_every_citation_kind
  ✓ test_validator_rejects_unknown_type
  ✓ test_validator_rejects_out_of_range_confidence
  ✓ test_validator_rejects_missing_required_fields
  ✓ test_validator_rejects_too_short_description
  ✓ test_validator_truncates_oversized_description
  ✓ test_validator_flags_next_step_with_no_action_verb
  ✓ test_validator_rejects_lead_with_zero_citations
  ✓ test_validator_clears_invalid_addresses_gap
  ✓ test_batch_validator_separates_good_from_bad
============================================================
  43 passed · 0 failed
============================================================
```

With Ollama running, the live integration is one more line:

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/investigator/cases/C-2053/leads/generate
```

## FYP defense talking points

**Q: How do you prevent hallucination?**
Three-layer defense: (1) every identifier the LLM sees is pre-indexed into eight `ctx.all_*_ids` sets; (2) the validator drops any lead citing an identifier not in those sets; (3) leads with zero citations are auto-rejected as "generic". 43 unit tests verify each layer in isolation. Logged audit trail per rejection.

**Q: Where do the leads come from — the model or the rules?**
Both, by design. The deterministic gap engine (`_compute_gaps`) does 80% of the investigative reasoning — surfacing 18 categories of inconsistency the system can detect by SQL alone. The LLM does the 20% the rules can't: writing investigator-readable narrative + spotting cross-statement contradictions + ordering by priority. Either layer can be inspected independently.

**Q: Why Llama 3.1 8B specifically?**
Q4_K_M quantization runs on a 16 GB laptop GPU (or CPU) without external API calls — matches the SDP's "no live integrations" constraint. Smaller models drop too many citations; 70B is overkill for structured-input tasks.

**Q: What about the SDP's "forensic out of scope" clause?**
We respect it. No DNA/AFIS/biometric processing is invented. "Forensic" leads only cite rows that already exist in `victim_forensic_findings` (free-text findings entered by the investigator) plus evidence-status flags (analyzed vs pending). No fabricated match scores anywhere in the codebase.

**Q: What's the latency budget?**
Context build ≈ 50 ms (4 SQL queries with joinedload). LLM call ≈ 4–6 s on q4_K_M for a typical case (7 KB prompt). Total ≈ 5–7 s. Streamed via NDJSON so the user sees leads appear progressively rather than after the full batch.
