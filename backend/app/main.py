"""Tendly FastAPI app entrypoint."""
from __future__ import annotations

import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .routes import patients, requests, summaries, tasks
from .seed import seed
from .ws import manager

logging.basicConfig(level=logging.INFO)
settings = get_settings()

# Sentry init (§3.8). Implemented by the observability/error-tracking feature;
# safe no-op when SENTRY_DSN is unset.
if settings.sentry_dsn:
    try:
        import sentry_sdk

        sentry_sdk.init(dsn=settings.sentry_dsn, traces_sample_rate=0.1)
    except Exception:  # pragma: no cover
        logging.getLogger("tendly").warning("Sentry init failed", exc_info=True)

app = FastAPI(title="Tendly", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(requests.router)
app.include_router(patients.router)
app.include_router(summaries.router)
app.include_router(tasks.router)


@app.on_event("startup")
async def _startup() -> None:
    seed()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "mocks": settings.allow_mocks}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()  # keepalive; we only push to clients
    except WebSocketDisconnect:
        await manager.disconnect(ws)
