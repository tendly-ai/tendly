"""Deepgram Voice Agent configuration (browser-side agent).

The agent itself runs in the patient's browser via `@deepgram/sdk`. This module
is the server-side half: it (1) mints a short-lived Deepgram token so the
long-lived `DEEPGRAM_API_KEY` never reaches the client, and (2) builds a
*personalized* agent configuration (prompt + greeting + model choices +
function schema) from the patient's Redis profile so the agent sounds like it
already knows them.

The agent gathers context conversationally and, once confident, calls the
`create_care_request` function — which the browser forwards to the existing
`POST /api/requests` endpoint, keeping Claude triage authoritative.
"""
from __future__ import annotations

import logging

import httpx

from ..config import get_settings
from ..models import PatientProfile
from . import memory

settings = get_settings()
logger = logging.getLogger("tendly.voice_agent")

DEEPGRAM_GRANT_URL = "https://api.deepgram.com/v1/auth/grant"

# How much recent history to surface to the agent (vs. 5 for triage).
AGENT_TRANSCRIPT_LIMIT = 15

# ---------------------------------------------------------------------------
# Persona + behavior prompt (combines warm talk-back tone with triage safety).
# ---------------------------------------------------------------------------
BASE_PERSONA = """\
You are Tendly, a warm, patient voice companion for an elderly resident in a \
care facility. You speak like a kind helper sitting nearby: calm, brief, and \
reassuring. Keep spoken replies to one or two short sentences.

YOUR JOB
- Listen to what the resident needs and make sure a caregiver or the system \
acts on it.
- Be conversational. If a request is vague, ambiguous, or could be a health \
concern, ask ONE short clarifying question before deciding. Do not interrogate \
— ask only what you genuinely need.
- Once you understand the request, call the `create_care_request` function with \
a clear, first-person paraphrase of what the resident wants. Then tell them \
warmly what is happening next.

SAFETY (non-negotiable)
- You are NOT a doctor. Never diagnose, prescribe, or give medical advice.
- If the resident mentions pain, a fall, dizziness, breathing trouble, or sounds \
distressed, do NOT ask lots of questions — reassure them briefly and call \
`create_care_request` right away so a caregiver is alerted. When unsure, escalate.

CONFIRMATION
- The system may tell you (in the function result) that an action needs \
confirmation. If so, clearly tell the resident what you're about to do and ask \
if you should go ahead. If they say yes, call `confirm_action` with confirmed=true; \
if no, call it with confirmed=false. The resident may also tap on-screen Yes/No \
buttons instead.

PERSONALIZATION (important — you already know this resident)
- The "WHO YOU ARE TALKING TO" section below contains real facts about THIS \
resident. Treat it as your own memory of them — never ask for things you already \
know (their name, room, family members, interests, or usual requests).
- Always address them by their preferred name (check Notes for what they like to \
be called).
- Keep your FIRST reply short and focused on what they need — do not open with \
small talk. Take care of their concern first.
- Only once their main need is understood or handled, AND the situation is calm \
and not urgent, you may warmly bring in something personal in a later turn (around \
the second or third message) — an interest, a favorite team, a preference. Keep it \
to a quick, natural touch, never a recital, and never at the expense of the request.
- If anything is urgent, medical, or distressing, skip the personal chit-chat \
entirely and stay focused on getting them help.
- When the resident refers to family only by relationship (e.g. "my daughter"), \
fill in the real name from the Family list (e.g. Sarah). But the Family list is \
NOT their full address book — they also have friends and other people in their \
phone's contacts who are not listed here.
- If the resident names a specific person to call or text (e.g. "text Henry"), \
use that EXACT name in `create_care_request`. Never swap it for a family member, \
and never tell them the person "isn't in your contacts" — the system looks people \
up in the device's contacts, so just pass along the name they said.

STYLE
- Speak clearly and a little slowly if notes mention hearing difficulty.
- Keep replies to one or two short, warm sentences.
- Never mention dashboards, APIs, automation, mocks, or internal systems.
"""


def _first_name(patient: PatientProfile) -> str:
    return patient.name.split()[0] if patient.name else "there"


def build_agent_prompt(patient_id: str) -> str:
    """Persona + safety rules + the patient's personalized Redis context."""
    context = memory.get_patient_context(
        patient_id, transcript_limit=AGENT_TRANSCRIPT_LIMIT
    )
    if context:
        return f"{BASE_PERSONA}\nWHO YOU ARE TALKING TO\n{context}\n"
    return BASE_PERSONA


def build_greeting(patient_id: str) -> str:
    patient = memory.get_patient(patient_id)
    if patient is None:
        return "Hello, I'm here to help. What do you need?"
    return f"Hi {_first_name(patient)}, how can I help you?"


# ---------------------------------------------------------------------------
# Function schema exposed to the agent (executed by the browser).
# ---------------------------------------------------------------------------
def _functions() -> list[dict]:
    return [
        {
            "name": "create_care_request",
            "description": (
                "Log the resident's need so a caregiver or the system can act on "
                "it. Call this once you understand what they want, after asking a "
                "clarifying question only if needed. For anything urgent or "
                "medical, call immediately."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "transcript": {
                        "type": "string",
                        "description": (
                            "A clear, first-person paraphrase of what the resident "
                            "is asking for, in their own words where possible. "
                            "E.g. 'Please help me call my daughter Sarah.'"
                        ),
                    }
                },
                "required": ["transcript"],
            },
        },
        {
            "name": "confirm_action",
            "description": (
                "Confirm or cancel a pending action that required confirmation. "
                "Use the request_id returned by create_care_request."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "request_id": {
                        "type": "string",
                        "description": "The request_id returned by create_care_request.",
                    },
                    "confirmed": {
                        "type": "boolean",
                        "description": "true if the resident agreed, false if they declined.",
                    },
                },
                "required": ["request_id", "confirmed"],
            },
        },
    ]


def build_agent_config(patient_id: str) -> dict:
    """The `agent` block of the Deepgram Settings message.

    The browser merges this with its own `audio` block (encoding/sample rate are
    client concerns) and sends it via `connection.sendSettings`.
    """
    return {
        "language": "en",
        "listen": {
            "provider": {
                "type": "deepgram",
                "model": settings.agent_listen_model,
            }
        },
        "think": {
            "provider": {
                "type": settings.agent_think_provider,
                "model": settings.agent_think_model,
            },
            "prompt": build_agent_prompt(patient_id),
            "functions": _functions(),
        },
        "speak": {
            "provider": {
                "type": "deepgram",
                "model": settings.agent_speak_model,
            }
        },
        "greeting": build_greeting(patient_id),
    }


# ---------------------------------------------------------------------------
# Short-lived token minting (keeps DEEPGRAM_API_KEY off the client).
# ---------------------------------------------------------------------------
async def mint_token() -> dict | None:
    """Mint a short-lived Deepgram access token. Returns {access_token, expires_in}
    or None when no key is configured (mock mode)."""
    if not settings.deepgram_api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                DEEPGRAM_GRANT_URL,
                headers={"Authorization": f"Token {settings.deepgram_api_key}"},
                json={"ttl_seconds": settings.agent_token_ttl},
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "access_token": data.get("access_token"),
                "expires_in": data.get("expires_in", settings.agent_token_ttl),
            }
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "Deepgram token mint returned %s (key may lack token-grant scope); "
            "falling back per AGENT_ALLOW_RAW_KEY.",
            exc.response.status_code,
        )
        return None
    except Exception as exc:
        logger.warning("Deepgram token mint failed: %s", exc)
        return None
