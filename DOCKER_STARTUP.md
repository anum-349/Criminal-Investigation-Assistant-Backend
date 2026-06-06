# 🚀 Docker Startup Guide

## Prerequisites

1. **Docker Desktop installed and running**
   - On Windows/Mac: Start Docker Desktop application
   - On Linux: Ensure Docker daemon is running
   ```bash
   docker --version
   ```

2. **Environment variables configured**
   ```bash
   # Copy the example file
   cp .env.example .env
   
   # Edit .env with your actual values
   # Required: GROQ_FIR_API_KEY, JWT_SECRET
   ```

---

## Startup Steps

### 1. Ensure Docker Desktop is Running

**Windows/Mac:**
- Open Docker Desktop application
- Wait for it to say "Docker is running" in the system tray

**Linux:**
```bash
sudo systemctl start docker
# Or for Docker socket:
sudo systemctl enable docker
```

### 2. Start the Application

```bash
# Build and start all services
docker compose up --build

# Or just start (if already built)
docker compose up

# In detached mode (background)
docker compose up -d --build
```

### 3. Verify Services are Running

```bash
# Check running containers
docker ps

# Expected output:
# cia-postgres    (port 5432)
# cia-backend     (port 8000)
```

### 4. Access the Application

- **API Docs:** http://localhost:8000/docs
- **Health Check:** http://localhost:8000/health
- **Database:** localhost:5432 (credentials in .env)

---

## Troubleshooting

### Docker Desktop Won't Start

**Windows:**
```bash
# Check if Docker service is running
Get-Service -Name "Docker Desktop Service"

# Restart the service
Restart-Service -Name "Docker Desktop Service" -Force

# Or restart Docker Desktop app manually
```

**Mac:**
- Force-quit Docker Desktop
- Reopen the application
- Wait 30 seconds for it to start

**Linux:**
```bash
sudo systemctl restart docker
sudo usermod -aG docker $USER
# Log out and back in for group changes to take effect
```

### Port Already in Use

If 5432 or 8000 is already in use:

```bash
# Stop existing services
docker compose down

# Or change ports in docker-compose.yml
# postgres: "5433:5432"
# backend: "8001:8000"
```

### Database Connection Fails

```bash
# Check database logs
docker compose logs postgres

# Verify database is healthy
docker compose logs --follow postgres
```

### Build Fails

```bash
# Clean build (remove old images)
docker compose down --remove-orphans
docker system prune -f
docker compose up --build

# Check build logs
docker compose logs backend
```

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GROQ_FIR_API_KEY` | ✅ Yes | Groq API key for LLM extraction |
| `JWT_SECRET` | ✅ Yes | Secret key for JWT token signing |
| `DB_PASSWORD` | ✅ Yes | PostgreSQL password |
| `LOG_LEVEL` | ❌ No | INFO, DEBUG, WARNING (default: INFO) |
| `CORS_ORIGINS` | ❌ No | Frontend URLs (default: localhost:5173, localhost:3000) |

---

## Common Commands

```bash
# Start services in background
docker compose up -d

# Stop all services
docker compose down

# View logs
docker compose logs -f backend
docker compose logs -f postgres

# Access backend shell
docker exec -it cia-backend bash

# Access database
docker exec -it cia-postgres psql -U cia_user -d cia_db

# Rebuild specific service
docker compose up --build backend

# Remove all data (⚠️ warning: deletes database)
docker compose down -v
```

---

## Development Workflow

### Option 1: Docker (Recommended for Production)
```bash
docker compose up --build
```

### Option 2: Local Python (For Development)
```bash
# Install dependencies
pip install -r requirements.txt

# Create .env file
cp .env.example .env
# Edit .env with your values

# Start PostgreSQL only (in Docker)
docker compose up postgres -d

# Run app locally
python main.py
```

---

## Health Check

Once running, verify everything is working:

```bash
# API is up
curl http://localhost:8000/health

# Database is up
docker exec -it cia-postgres pg_isready -U cia_user

# Check all services
docker compose ps
```

Expected output for `curl http://localhost:8000/health`:
```json
{
  "status": "ok",
  "models": "ready"
}
```

---

**For issues, check logs:**
```bash
docker compose logs --tail=50 backend
```
