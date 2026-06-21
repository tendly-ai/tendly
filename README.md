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

Frontend (browser):
```bash
cd frontend
npm install
npm run dev                  # http://localhost:3000
```

### Mock mode

With `TENDLY_ALLOW_MOCKS=true` (default), every integration falls back to a
deterministic mock when its API key is missing, so the full app runs end-to-end
without any credentials. Set keys in `.env` to use the real services.

### Simulang automation

Confirmed automation tasks run through the real Simulang CLI:

```bash
npm install -g @simular-ai/simulang  # requires Node.js 22.18+
simulang setup                       # macOS Accessibility/Input/Screen prompts
```

The backend writes generated TypeScript scripts under
`frontend/.simulang-tasks/` and executes them with `simulang run`. The scripts
import `@simular-ai/simulang-js`, open or focus the target app, inspect the
accessibility tree, and act on refIds where possible. With
`TENDLY_ALLOW_MOCKS=true`, a missing or failing Simulang runtime returns a mock
detail instead of silently using a non-Simulang local handoff.

## Desktop app (Electron)

### Dev mode

Start the backend first (it is not auto-spawned in dev mode):

```bash
cd backend && ./run.sh
```

Then in a second terminal:

```bash
cd frontend
npm run electron:dev
```

This starts the Next.js dev server and opens Electron pointing at `localhost:3000`.
Hot-reload works normally — save a file and the window refreshes.

### Production build

```bash
cd frontend
npm run electron:build
```

This runs `next build` with static export enabled, then packages with
`electron-builder`. The output lands in `frontend/dist-electron/`.

On macOS you get a `.dmg`; on Windows a `.exe` installer (NSIS).

> **Backend in production:** by default the packaged app expects the FastAPI
> server to be running separately. To have Electron spawn it automatically,
> set the env var `SPAWN_BACKEND=true` before launching — Electron will start
> `uvicorn` from `backend/.venv/bin/python` and kill it on quit.

### New dependencies (devDependencies, frontend only)

| Package | Purpose |
|---|---|
| `electron` | Desktop shell |
| `electron-builder` | Cross-platform packaging (DMG / NSIS) |
| `concurrently` | Run Next.js dev server + Electron simultaneously |
| `wait-on` | Waits for `localhost:3000` before opening the Electron window |

System requirements for building: Node 18+, macOS 12+ (for DMG), Windows 10+ (for NSIS).

## Feature branches

This project was built feature-by-feature on separate branches merged into the
integration branch:

- Patient Interface · Caregiver Dashboard · AI Triage (Claude + Deepgram) ·
  Simular Automation · Redis Memory · Family Summary · Observability (Arize) + Sentry
