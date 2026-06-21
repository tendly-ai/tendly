"""Warm patient-facing talk-back copy.

These messages are spoken aloud through Deepgram TTS, so they should sound like
a calm helper sitting nearby: brief, concrete, and reassuring without making
medical promises.
"""
from __future__ import annotations

import logging

from ..config import get_settings
from ..models import CareRequest, Category

settings = get_settings()
logger = logging.getLogger("tendly.talkback")

TALKBACK_PROMPT = """\
Rewrite the draft as one short, warm spoken response for an elderly patient.

Style:
- Natural and heartfelt, like a kind helper.
- One or two sentences. Under 35 words.
- Concrete about what is happening next.
- No medical advice, diagnoses, or promises about exact timing.
- Do not mention dashboards, APIs, automation, mocks, or internal systems.
- Return only the response text.
"""


def _first_name(req: CareRequest) -> str:
    return req.patient_name.split()[0] if req.patient_name else "there"


def _fallback(kind: str, req: CareRequest, plan: str = "") -> str:
    name = _first_name(req)

    if kind == "confirm":
        return f"I can help with that, {name}. Before I do it, I want to make sure: {plan} Should I go ahead?"

    if kind == "automation_done":
        if req.category == Category.family_communication:
            return f"Of course, {name}. I'll help you reach your family now."
        return f"Of course, {name}. I'll take care of that for you now."

    if kind == "automation_tried":
        return f"I tried to help with that, {name}, but it may need a little extra attention."

    if kind == "cancelled":
        return f"No worries, {name}. I cancelled that for you."

    if kind == "caregiver":
        if req.category == Category.urgent_medical:
            return f"I hear you, {name}. I've let the care team know so someone can check on you."
        if req.category == Category.in_person_caregiver:
            return f"Okay, {name}. I've let the care team know what you need."
        return f"Thanks for telling me, {name}. I've passed that along for you."

    return f"I'm here with you, {name}. I'll help with that."


async def _polish(kind: str, req: CareRequest, draft: str) -> str:
    if not settings.anthropic_api_key:
        return draft

    from anthropic import AsyncAnthropic  # type: ignore

    try:
        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        resp = await client.messages.create(
            model=settings.anthropic_model,
            max_tokens=90,
            system=TALKBACK_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Situation: {kind}\n"
                        f"Patient: {req.patient_name}\n"
                        f"Patient said: {req.transcript!r}\n"
                        f"Category: {req.category.value}\n"
                        f"Draft: {draft}"
                    ),
                }
            ],
        )
        text: str = resp.content[0].text  # type: ignore[union-attr]
        text = " ".join(text.replace('"', "").split())
        if 0 < len(text) <= 220:
            return text
    except Exception as exc:
        logger.warning("Talk-back polish failed; using fallback: %s", exc)

    return draft


async def confirmation(req: CareRequest, plan: str) -> str:
    return await _polish("confirm", req, _fallback("confirm", req, plan))


async def automation_done(req: CareRequest) -> str:
    return await _polish("automation_done", req, _fallback("automation_done", req))


async def automation_tried(req: CareRequest) -> str:
    return await _polish("automation_tried", req, _fallback("automation_tried", req))


async def cancelled(req: CareRequest) -> str:
    return await _polish("cancelled", req, _fallback("cancelled", req))


async def caregiver_notified(req: CareRequest) -> str:
    return await _polish("caregiver", req, _fallback("caregiver", req))
