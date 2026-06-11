# Criminal Investigation Assistant Backend

An AI-powered FastAPI backend for criminal investigation case management with intelligent case linking, FIR document processing, and real-time notifications.

---

## 🎯 Overview

The Criminal Investigation Assistant is a comprehensive backend system designed to:
- **Manage criminal cases** with detailed suspect, victim, and witness information
- **Process FIR documents** using OCR and LLM-powered extraction (Groq)
- **Link related cases** using AI-powered semantic similarity
- **Generate insights** on case completeness and missing information
- **Track investigations** with timelines, evidence, and audit logs
- **Enable real-time collaboration** via WebSocket notifications

---

## 🚀 Quick Start

### Prerequisites
- Docker & Docker Compose
- Python 3.11+ (for local development)
- Git

### 1. Clone & Setup

```bash
git clone <repo-url>
cd Criminal_Investigation_Assistant_Backend
```

### 2. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` with your actual values:
```env
DB_PASSWORD=aqdus102
SQLALCHEMY_DATABASE_URL=postgresql://cia_user:aqdus102@postgres:5432/criminal_investigation_assistant
GROQ_API_KEY=your_groq_api_key_here
JWT_SECRET=your_jwt_secret_here
```

### 3. Start with Docker

```bash
# Build and start all services
docker compose up --build

# Or just start (if already built)
docker compose up

# Start in background
docker compose up -d
```

### 4. Verify It's Running

```bash
# Check containers
docker compose ps

# Test API
curl http://localhost:8000/health

# View API docs
open http://localhost:8000/docs
```

---

## 📁 Project Structure

```
.
├── main.py                      # FastAPI entry point
├── models.py                    # SQLAlchemy ORM models (12 sections)
├── db.py                        # Database configuration
│
├── dependencies/
│   └── auth.py                  # Centralized JWT authentication
│
├── routes/                      # API endpoints (organized by domain)
│   ├── cases/                   # Case management
│   ├── investigations/          # FIR processing
│   ├── leads/                   # AI-generated insights
│   ├── admin/                   # User & system management
│   ├── notifications/           # WebSocket & REST notifications
│   └── account/                 # Auth & profile
│
├── services/                    # Business logic (organized by domain)
│   ├── cases/                   # Case registration, detail, drafts
│   ├── persons/                 # Suspects, victims, witnesses, locations
│   ├── leads/                   # Lead generation, predictions
│   ├── admin/                   # Users, tickets, reports, analytics
│   ├── search/                  # Case linking, similarity
│   └── common/                  # Shared utilities (auth, audit, notifications)
│
├── schemas/                     # Pydantic request/response models (by entity)
│   ├── cases/
│   ├── persons/
│   ├── leads/
│   ├── admin/
│   └── common/
│
├── fir_api/                     # FIR document processing pipeline
│   ├── pipeline/
│   │   ├── orchestrator.py      # Groq LLM extraction
│   │   ├── model_loader.py      # ML model loading
│   │   ├── entity_extractor.py  # NER with spaCy
│   │   └── ...
│   └── models/                  # Pre-downloaded ML models
│
├── alembic/                     # Database migrations (currently disabled)
├── docker-compose.yml           # Container orchestration
├── Dockerfile                   # Docker image definition
└── requirements.txt             # Python dependencies
```

---

## 🗄️ Database

### Schema Overview

**61 tables** organized into logical groups:

| Group | Purpose | Key Tables |
|-------|---------|-----------|
| **Lookup Tables** | Enumerations | lkp_case_types, lkp_case_statuses, lkp_severities, etc. |
| **Auth** | Users & roles | users, user_roles, user_sessions, password_reset_tokens |
| **Core** | Case entity | cases, persons, locations, evidence |
| **Relationships** | Case roles | case_suspects, case_victims, case_witnesses |
| **Crime Details** | Subtypes | murder_details, sexual_assault_details, theft_details |
| **AI Module** | ML outputs | leads, case_embeddings, extracted_entities, case_links |
| **Timeline** | History | timeline_events, case_update_notes, investigation_notes |
| **Admin** | Operations | audit_logs, notifications, tickets, backup_records |

### Database URL

```
postgresql://cia_user:aqdus102@postgres:5432/criminal_investigation_assistant
```

- **Host:** `postgres` (Docker) or `localhost` (local dev)
- **User:** `cia_user`
- **Password:** `aqdus102` (from `.env`)
- **Database:** `criminal_investigation_assistant`

---

## 🔐 Authentication

### JWT Token Flow

1. **Login** → User provides username/password
2. **Token Creation** → Backend generates JWT (24h expiry)
3. **Token Usage** → Client sends token in `Authorization: Bearer <token>` header
4. **Validation** → Middleware verifies token & extracts user

### User Roles

- **admin** — Full system access, user management
- **investigator** — Case management, lead review
- **supervisor** — Case oversight, reporting

### Default Test Users

After seeding, three users are available:

```
Username: admin           | Password: hashedpwd | Role: admin
Username: investigator1   | Password: hashedpwd | Role: investigator
Username: investigator2   | Password: hashedpwd | Role: investigator
```

---

## 📊 API Endpoints

### Cases
- `POST /api/cases/register` — Register new case (with FIR file or manual entry)
- `GET /api/cases/{case_id}` — Retrieve full case details
- `GET /api/cases` — List all cases (paginated, filterable)
- `PUT /api/cases/{case_id}` — Update case information
- `DELETE /api/cases/{case_id}` — Archive case

### Persons
- `POST /api/persons` — Register suspect, victim, or witness
- `GET /api/persons/{person_id}` — Get person details
- `PUT /api/persons/{person_id}` — Update person info
- `GET /api/persons/search?query=...` — Search by CNIC, name, etc.

### Leads
- `GET /api/leads?case_id=...` — Get AI-generated leads for a case
- `PUT /api/leads/{lead_id}` — Mark lead as reviewed/resolved

### Case Linking
- `POST /api/cases/{case_id}/link` — Link two cases (AI-powered)
- `GET /api/cases/{case_id}/linked` — Get linked cases

### Notifications
- WebSocket: `ws://localhost:8000/ws/{user_id}` — Real-time updates
- `GET /api/notifications` — Fetch notification history
- `PUT /api/notifications/{notification_id}/read` — Mark as read

### Admin
- `GET /api/admin/users` — List all users
- `POST /api/admin/users` — Create new user
- `GET /api/admin/reports` — Generate system reports
- `GET /api/admin/audit-logs` — View audit trail

### Full API Documentation
Open `http://localhost:8000/docs` (Swagger UI) after starting the server.

---

## 🤖 AI Features

### 1. FIR Document Processing

**Input:** PDF/image of FIR document  
**Output:** Structured case data

```
OCR (EasyOCR) → Groq LLM (llama-3.3-70b) → Structured Extraction
```

**Extracted Fields:**
- FIR number, date, reporting officer
- Incident details, location, crime type
- Suspects, victims, witnesses (preliminary)
- Case narrative

### 2. Case Linking

**Input:** Case details  
**Output:** Similar cases + confidence scores

```
Case Narrative → Embeddings (Sentence-BERT) → Cosine Similarity → Ranked Results
```

**Uses:**
- Find related past cases
- Identify suspect patterns
- Detect organized crime networks

### 3. Lead Generation

**Input:** Case update  
**Output:** Actionable leads

- Suggests suspects based on similar cases
- Flags missing investigative steps
- Recommends witness follow-ups

### 4. Completeness Scoring

**Input:** Case data  
**Output:** Completeness %, missing fields

Tracks:
- Required vs. filled fields
- Data inconsistencies
- Investigation progress

---

## 📥 Seeding Data

### Option 1: SQL Seed (Fast)

```bash
docker cp seed.sql cia-postgres:/seed.sql
docker exec cia-postgres psql -U cia_user -d criminal_investigation_assistant -f /seed.sql
```

### Option 2: Python Seed Script

```bash
docker exec cia-backend python seed_db.py
```

### Default Seeded Data

- 3 users (admin, investigator1, investigator2)
- 5 sample persons (suspects, victims, witnesses)
- Pre-configured lookup values

---

## 🐳 Docker Deployment

### Build Image

```bash
docker compose build --no-cache
```

### Run Containers

```bash
# Start all services
docker compose up -d

# View logs
docker compose logs backend -f
docker compose logs postgres -f

# Stop services
docker compose down

# Full cleanup (removes volumes)
docker compose down -v
```

### Container Ports

| Service | Port | Purpose |
|---------|------|---------|
| Backend (FastAPI) | 8000 | API & Docs |
| PostgreSQL | 5432 | Database |

---

## 💻 Local Development

### Without Docker

```bash
# 1. Start PostgreSQL only (in Docker)
docker compose up postgres -d

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env with localhost for database host

# 4. Run locally
python main.py
```

### Auto-Reload on Code Changes

```bash
# Using uvicorn directly
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

---

## 🛠️ Configuration

### Environment Variables

```env
# Database
SQLALCHEMY_DATABASE_URL=postgresql://cia_user:PASSWORD@HOST:5432/criminal_investigation_assistant
DB_PASSWORD=your_password

# Authentication
JWT_SECRET=your_secret_key_here
JWT_ALGORITHM=HS256
JWT_EXPIRY_HOURS=24

# Groq LLM
GROQ_API_KEY=your_groq_api_key
GROQ_MODEL=llama-3.3-70b-versatile

# ML Models
FIR_EMBEDDING_MODEL=paraphrase-multilingual-MiniLM-L12-v2
FIR_SPACY_EN_MODEL=en_core_web_sm
HF_HOME=/root/.cache/huggingface

# Application
LOG_LEVEL=INFO
AUTO_CREATE_TABLES=true
RUN_MIGRATIONS=false
PYTHONUNBUFFERED=1

# CORS
CORS_ORIGINS=http://localhost:5173,http://localhost:3000
```

---

## 🧪 Testing

### Run Tests

```bash
# All tests
pytest

# Specific test file
pytest tests/test_cases.py

# With coverage
pytest --cov=.
```

### Manual Testing

```bash
# Health check
curl http://localhost:8000/health

# Login
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"hashedpwd"}'

# List cases
curl http://localhost:8000/api/cases \
  -H "Authorization: Bearer YOUR_TOKEN"
```

---

## 📋 Project Status

### ✅ Completed

- Core case management CRUD
- User authentication & roles
- FIR document processing (Groq + OCR)
- Case linking with semantic similarity
- Real-time WebSocket notifications
- Comprehensive audit logging
- 61 optimized database tables
- Organized service/route/schema architecture

### ⚠️ Known Limitations

- Alembic migrations currently disabled (using auto table creation)
- Urdu language processing requires additional setup
- WebSocket limited to single-server deployment

### 🚧 Future Enhancements

- Kubernetes deployment templates
- Advanced analytics dashboard
- Multi-language FIR support
- Distributed WebSocket (Redis pub/sub)
- Machine learning model retraining pipeline

---

## 🆘 Troubleshooting

### Database connection fails

```bash
# Check if PostgreSQL is running
docker compose ps

# Verify credentials in .env
docker exec cia-postgres psql -U cia_user -d criminal_investigation_assistant -c "SELECT 1"

# Check Docker network
docker network inspect criminal_investigation_assistant_backend_cia-net
```

### Backend won't start

```bash
# Check logs
docker compose logs backend --tail 50

# Common issues:
# - Missing GROQ_API_KEY → Set in .env
# - Missing JWT_SECRET → Set in .env
# - Database not ready → Wait 5 seconds, restart
```

### Slow model loading

First startup loads ML models (~15 min). Subsequent starts use cache (< 1 min).

```bash
# Monitor progress
docker compose logs backend -f
```

### Port 8000 already in use

```bash
# Change port in docker-compose.yml
# Change: ports: - "8001:8000"
# Then: docker compose up
```

---

## 📚 Key Technologies

| Layer | Technology |
|-------|-----------|
| **Framework** | FastAPI (Python 3.11) |
| **ORM** | SQLAlchemy 2.0 |
| **Database** | PostgreSQL 16 |
| **Auth** | JWT (PyJWT) |
| **Real-time** | WebSocket (starlette) |
| **LLM** | Groq API (llama-3.3-70b) |
| **NER** | spaCy en_core_web_sm |
| **Embeddings** | Sentence-BERT (multilingual) |
| **OCR** | EasyOCR |
| **Validation** | Pydantic v2 |
| **Container** | Docker & Docker Compose |

---

## 📝 API Response Format

### Success Response

```json
{
  "status": "success",
  "data": { /* response payload */ },
  "message": "Operation completed"
}
```

### Error Response

```json
{
  "status": "error",
  "error": "NOT_FOUND",
  "message": "Case not found",
  "details": { /* additional info */ }
}
```

---

## 🔗 Useful Links

- **API Docs:** http://localhost:8000/docs
- **ReDoc:** http://localhost:8000/redoc
- **Health:** http://localhost:8000/health
- **Groq API Docs:** https://console.groq.com/docs
- **FastAPI Docs:** https://fastapi.tiangolo.com/

---

## 📄 License

[Add your license here]

---

## 👥 Contributing

[Add contribution guidelines]

---

## 📞 Support

For issues or questions, contact: [your-email@example.com]

---

**Last Updated:** 2026-06-07  
**Version:** 0.1.0  
**Status:** Development
