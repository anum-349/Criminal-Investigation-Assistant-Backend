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