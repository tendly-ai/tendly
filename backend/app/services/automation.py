"""Computer automation via Simular `simulang` (§3.4).

CONTRACT: `run_task(req) -> dict` executes a digital task for the patient and
returns {status, detail}. The Simular feature subagent integrates the real
`simulang` library (https://github.com/simular-ai/simulang). This scaffold
returns a deterministic mock describing what WOULD happen, with the integration
point clearly marked.
"""
from __future__ import annotations

from ..config import get_settings
from ..models import CareRequest, Category

settings = get_settings()


def plan_task(req: CareRequest) -> str:
    """Human-readable description of the automated action to be taken."""
    t = req.transcript.lower()
    if req.category == Category.family_communication:
        return "Start a video call with the patient's family contact."
    if "email" in t:
        return "Open the patient's email client."
    if "music" in t or "play" in t:
        return "Open a music streaming service and play something."
    if "browse" in t or "website" in t or "internet" in t:
        return "Open the web browser to the requested page."
    return "Perform the requested digital task."


async def run_task(req: CareRequest) -> dict:
    """Execute the automated task. Returns {status, detail}."""
    plan = plan_task(req)
    if settings.allow_mocks:
        # Integration point: replace with real simulang agent run.
        return {"status": "mocked", "detail": f"[Simular mock] Would: {plan}"}

    # Real implementation (simulang). Implemented by the Simular feature.
    raise NotImplementedError("simulang integration not implemented yet")
