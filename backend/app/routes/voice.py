"""Voice Agent endpoints.

The Deepgram Voice Agent runs in the patient's browser. This route serves the
per-session config: a short-lived Deepgram token + a personalized agent block
built from the patient's Redis profile. The agent's function calls
(`create_care_request`, `confirm_action`) are forwarded by the browser to the
existing `/api/requests` and `/api/tasks/confirm` endpoints, so the Claude
triage pipeline stays authoritative.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..config import get_settings
from ..services import memory, voice_agent

router = APIRouter(prefix="/api/voice", tags=["voice"])
settings = get_settings()


@router.get("/agent-config/{patient_id}")
async def agent_config(patient_id: str):
    """Return everything the browser needs to start a personalized agent session.

    `auth_type` tells the client how to authenticate the agent socket:
      - "bearer": short-lived JWT from token minting (preferred)
      - "token":  raw API key (local-dev fallback when minting is not permitted)
    """
    patient = memory.get_patient(patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail=f"Unknown patient {patient_id}")

    agent = voice_agent.build_agent_config(patient_id)

    if not settings.deepgram_api_key:
        return {
            "enabled": False,
            "reason": "DEEPGRAM_API_KEY not configured",
            "agent": agent,
        }

    token = await voice_agent.mint_token()
    if token and token.get("access_token"):
        return {
            "enabled": True,
            "auth_type": "bearer",
            "token": token["access_token"],
            "expires_in": token["expires_in"],
            "agent": agent,
        }

    # Minting failed (e.g. key lacks the scope to grant tokens).
    if settings.agent_allow_raw_key:
        return {
            "enabled": True,
            "auth_type": "token",
            "token": settings.deepgram_api_key,
            "agent": agent,
        }

    return {
        "enabled": False,
        "reason": (
            "Could not mint a Deepgram token (the API key may lack token-grant "
            "permission). Use a member-scoped key or set AGENT_ALLOW_RAW_KEY=true "
            "for local development."
        ),
        "agent": agent,
    }
