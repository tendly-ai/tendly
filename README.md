# Tendly — AI-Powered Elderly Care Assistant

Hackathon MVP (ticket **CARE-001**). Two interfaces over an AI triage backend:

- **Patient voice interface** (`/patient`) — one button, speak a request, get confirmation.
- **Caregiver dashboard** (`/dashboard`) — real-time, urgency-ranked queue of requests.

An AI triage layer (Claude) classifies urgency/category, enriches with patient
memory (Redis), routes automatable tasks to Simular, logs decisions to Arize, and
generates warm family summaries.

## Architecture

```
Patient (Next.js)  ──audio/text──►  FastAPI backend  ──websocket──►  Dashboard (Next.js)
                                     │ Deepgram STT
                                     │ Claude triage  ──► Arize log
                                     │ Redis memory
                                     │ Simular automation
```

## Layout

| Path | What |
|---|---|
| `backend/app/main.py` | FastAPI app, CORS, Sentry init, WebSocket `/ws` |
| `backend/app/models.py` | **Shared contract** — Pydantic schemas all features depend on |
| `backend/app/routes/` | REST endpoints (`/api/requests`, `/api/patients`, `/api/summaries`, `/api/tasks`) |
| `backend/app/services/stt.py` | Deepgram speech-to-text |
| `backend/app/services/triage.py` | Claude triage (classification, urgency, routing) |
| `backend/app/services/memory.py` | Redis short/long-term memory |
| `backend/app/services/automation.py` | Simular `simulang` computer automation |
| `backend/app/services/summary.py` | Claude family summaries |
| `backend/app/services/observability.py` | Arize logging |
| `backend/app/seed.py` | Mock data: 3 patients + 2 caregivers |
| `frontend/app/patient/` | Patient voice UI |
| `frontend/app/dashboard/` | Caregiver dashboard |
| `frontend/lib/` | API client, types, websocket helpers |

## Running locally

Backend:
```bash
cd backend
cp ../.env.example ../.env   # fill in keys (optional; mocks work without them)
./run.sh                     # http://localhost:8000  (docs at /docs)
```

Frontend:
```bash
cd frontend
npm install
npm run dev                  # http://localhost:3000
```

### Mock mode

With `TENDLY_ALLOW_MOCKS=true` (default), every integration falls back to a
deterministic mock when its API key is missing, so the full app runs end-to-end
without any credentials. Set keys in `.env` to use the real services.

## Feature branches

This project was built feature-by-feature on separate branches merged into the
integration branch:

- Patient Interface · Caregiver Dashboard · AI Triage (Claude + Deepgram) ·
  Simular Automation · Redis Memory · Family Summary · Observability (Arize) + Sentry
