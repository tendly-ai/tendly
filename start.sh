#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# ── Backend ──────────────────────────────────────────────────────────────────
echo "Starting backend..."
(
  cd backend
  python3 -m venv .venv 2>/dev/null || true
  source .venv/bin/activate
  # Install deps, skipping the pinned pydantic (incompatible with Python 3.13)
  grep -v "^pydantic" requirements.txt | pip install -q -r /dev/stdin
  pip install -q "pydantic>=2.9"
  uvicorn app.main:app --host 0.0.0.0 --port 8000 &
) &
BACKEND_PID=$!

# ── Wait for backend ─────────────────────────────────────────────────────────
echo "Waiting for backend on :8000..."
for i in $(seq 1 30); do
  curl -sf http://localhost:8000/health > /dev/null 2>&1 && break
  sleep 1
done
curl -sf http://localhost:8000/health > /dev/null 2>&1 || { echo "Backend failed to start"; exit 1; }
echo "Backend ready."

# ── Frontend + Electron ───────────────────────────────────────────────────────
echo "Starting Electron..."
cd frontend
npm install --silent
NODE_ENV=development npx electron .
