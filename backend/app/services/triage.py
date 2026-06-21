"""AI triage via Claude (§3.3).

CONTRACT: `triage(transcript, patient_context) -> TriageResult`. The AI-triage
feature subagent implements the real Claude call with the safety system prompt
and few-shot examples from §3.3.5, and wraps it with Arize logging
(observability.log_triage). This scaffold ships a deterministic keyword-based
mock so the dashboard shows realistic cards before Claude is wired up.
"""
from __future__ import annotations

import json
import logging
import re

from ..config import get_settings
from ..models import Category, TriageResult, Urgency

settings = get_settings()
logger = logging.getLogger("tendly.triage")

# ---------------------------------------------------------------------------
# System prompt — §3.3.3 safety rules, category/urgency definitions,
# and §3.3.5 few-shot reference examples.
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a triage assistant in an elderly care facility. You classify patient \
voice requests so caregivers can prioritize them.

SAFETY RULES (non-negotiable):
1. You are NOT a doctor. Never diagnose, prescribe, or give medical advice.
2. When in doubt, escalate. If a request is medically ambiguous or the patient \
sounds distressed, classify as urgent_medical with high or emergency urgency. \
False positives are acceptable; false negatives are not.
3. Never suppress a request. Every utterance produces a record, even chit-chat.
4. Confirm before sensitive automation (sending data, submitting forms, \
accessing private info): set requires_confirmation=true.

CATEGORIES (§3.3.1):
- urgent_medical: any health concern, symptom, medication issue, fall, pain, \
breathing difficulty.
- in_person_caregiver: needs a person to visit (toileting, repositioning, \
bringing items).
- routine_comfort: environmental adjustments the patient could almost do \
themselves (TV volume, temperature, lights).
- automated_task: digital actions a computer agent can handle (play music, \
browse the web, send email).
- family_communication: patient wants to reach a family member (call, video, \
text).
- general_conversation: chit-chat, reminiscence, no specific action needed.

URGENCY LEVELS (§3.3.2):
- emergency: life-threatening — fall with injury, chest pain, can't breathe, \
unconscious.
- high: not immediately life-threatening but needs prompt attention — dizziness, \
medication side-effects, new pain.
- medium: uncomfortable but stable — needs water, bathroom help, repositioning.
- low: no immediate need — comfort tweaks, conversation, digital tasks.

Return ONLY valid JSON (no markdown fences, no extra text) with exactly these keys:
{
  "category": "<one of the six categories>",
  "urgency": "<one of the four urgency levels>",
  "summary": "<1-2 sentence summary for the caregiver dashboard>",
  "suggested_action": "<concrete next step for the caregiver>",
  "requires_confirmation": <true or false>,
  "reasoning": "<brief internal reasoning for logging>"
}

REFERENCE EXAMPLES (§3.3.5):

Patient: "I fell in the bathroom and I can't get up."
{
  "category": "urgent_medical",
  "urgency": "emergency",
  "summary": "Patient reports a fall in the bathroom and is unable to get up.",
  "suggested_action": "Go to the patient immediately, assess for injuries, and call emergency services if needed.",
  "requires_confirmation": false,
  "reasoning": "Fall with inability to rise — treat as emergency until assessed."
}

Patient: "I feel dizzy and I think it started after my new medication."
{
  "category": "urgent_medical",
  "urgency": "high",
  "summary": "Patient reports dizziness potentially related to a new medication.",
  "suggested_action": "Check on the patient soon, review current medications, and consult the prescribing physician.",
  "requires_confirmation": false,
  "reasoning": "Dizziness after medication change could indicate adverse reaction — escalate."
}

Patient: "Can you help me call my daughter?"
{
  "category": "family_communication",
  "urgency": "low",
  "summary": "Patient wants to contact their daughter.",
  "suggested_action": "Initiate a call to the patient's daughter using the contact on file.",
  "requires_confirmation": true,
  "reasoning": "Family call — confirm before dialing to respect privacy."
}

Patient: "I'm a bit cold, could someone bring me another blanket?"
{
  "category": "in_person_caregiver",
  "urgency": "medium",
  "summary": "Patient is cold and requesting an extra blanket.",
  "suggested_action": "Bring an additional blanket to the patient's room.",
  "requires_confirmation": false,
  "reasoning": "Physical comfort need requiring caregiver visit."
}

Patient: "Can you play some Frank Sinatra for me?"
{
  "category": "automated_task",
  "urgency": "low",
  "summary": "Patient wants to listen to Frank Sinatra.",
  "suggested_action": "Play Frank Sinatra music on the patient's room speaker.",
  "requires_confirmation": false,
  "reasoning": "Simple digital task, no sensitive data involved."
}

Patient: "You know, I was just thinking about my garden back home."
{
  "category": "general_conversation",
  "urgency": "low",
  "summary": "Patient is reminiscing about their garden.",
  "suggested_action": "Acknowledge warmly when convenient; note for family summary.",
  "requires_confirmation": false,
  "reasoning": "Conversational remark — no action required, log for family digest."
}

Now classify the following patient request."""

# ---------------------------------------------------------------------------
# Few-shot messages fed as conversation history for stronger adherence.
# ---------------------------------------------------------------------------
FEW_SHOT_MESSAGES: list[dict[str, str]] = [
    {
        "role": "user",
        "content": "Patient said: \"I feel dizzy and I think it started after my new medication.\"",
    },
    {
        "role": "assistant",
        "content": json.dumps(
            {
                "category": "urgent_medical",
                "urgency": "high",
                "summary": "Patient reports dizziness potentially related to a new medication.",
                "suggested_action": "Check on the patient soon, review current medications, and consult the prescribing physician.",
                "requires_confirmation": False,
                "reasoning": "Dizziness after medication change could indicate adverse reaction — escalate.",
            }
        ),
    },
    {
        "role": "user",
        "content": "Patient said: \"Can you help me call my daughter?\"",
    },
    {
        "role": "assistant",
        "content": json.dumps(
            {
                "category": "family_communication",
                "urgency": "low",
                "summary": "Patient wants to contact their daughter.",
                "suggested_action": "Initiate a call to the patient's daughter using the contact on file.",
                "requires_confirmation": True,
                "reasoning": "Family call — confirm before dialing to respect privacy.",
            }
        ),
    },
]


# ---------------------------------------------------------------------------
# Deterministic keyword mock (fallback when ANTHROPIC_API_KEY is missing).
# ---------------------------------------------------------------------------

def _mock_triage(transcript: str) -> TriageResult:
    t = transcript.lower()

    def has(*words: str) -> bool:
        return any(w in t for w in words)

    if has("fell", "fall", "chest pain", "can't breathe", "cant breathe",
           "bleeding", "passed out", "unconscious"):
        return TriageResult(
            category=Category.urgent_medical, urgency=Urgency.emergency,
            summary="Patient reports a possible medical emergency. Immediate response required.",
            suggested_action="Go to the patient immediately and assess.",
            reasoning="Emergency keyword detected (mock).",
        )
    if has("dizzy", "dizziness", "faint", "weak", "pain", "medication", "nausea", "hurt"):
        return TriageResult(
            category=Category.urgent_medical, urgency=Urgency.high,
            summary="Patient reports symptoms that need nurse assessment.",
            suggested_action="Check on the patient soon and review symptoms/medications.",
            reasoning="Medical-symptom keyword detected (mock).",
        )
    if has("call", "video", "message", "text", "daughter", "son", "family", "grandson", "granddaughter"):
        return TriageResult(
            category=Category.family_communication, urgency=Urgency.low,
            summary="Patient wants to contact a family member.",
            suggested_action="Help start a call/message to the patient's family contact.",
            requires_confirmation=True,
            reasoning="Family-contact keyword detected (mock).",
        )
    if has("email", "music", "play", "browse", "website", "open", "internet", "show", "tv channel"):
        return TriageResult(
            category=Category.automated_task, urgency=Urgency.low,
            summary="Patient wants help with a digital task.",
            suggested_action="Run the requested digital task via automation.",
            reasoning="Digital-task keyword detected (mock).",
        )
    if has("water", "blanket", "bathroom", "cold", "hot", "hungry", "thirsty", "reposition", "pillow"):
        return TriageResult(
            category=Category.in_person_caregiver, urgency=Urgency.medium,
            summary="Patient needs physical assistance.",
            suggested_action="Visit the patient to provide the requested help.",
            reasoning="Physical-assistance keyword detected (mock).",
        )
    if has("tv", "volume", "temperature", "light", "channel"):
        return TriageResult(
            category=Category.routine_comfort, urgency=Urgency.low,
            summary="Patient has a routine comfort request.",
            suggested_action="Adjust the patient's environment as requested.",
            reasoning="Comfort keyword detected (mock).",
        )
    return TriageResult(
        category=Category.general_conversation, urgency=Urgency.low,
        summary="Patient made a general remark. No action required, noted for family summary.",
        suggested_action="Acknowledge warmly when convenient.",
        reasoning="No actionable keyword detected (mock).",
    )


# ---------------------------------------------------------------------------
# JSON extraction helper — robust to markdown fences and stray prose.
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict:
    """Pull the first JSON object from *text*, tolerating markdown fences and
    surrounding prose."""
    # Strip markdown code fences if present.
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = text.replace("```", "")

    # Find the first { … } block.
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"No JSON object found in model output: {text!r}")
    return json.loads(text[start : end + 1])


# ---------------------------------------------------------------------------
# Main entry point.
# ---------------------------------------------------------------------------

async def triage(transcript: str, patient_context: str = "") -> TriageResult:
    """Classify a patient transcript into a TriageResult."""
    if not settings.anthropic_api_key:
        if settings.allow_mocks:
            return _mock_triage(transcript)
        raise RuntimeError("ANTHROPIC_API_KEY not configured")

    # Build the real Claude request.
    from anthropic import AsyncAnthropic  # type: ignore

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    # Construct the user message with optional patient context.
    if patient_context:
        user_msg = (
            f"Patient context:\n{patient_context}\n\n"
            f"Patient said: \"{transcript}\""
        )
    else:
        user_msg = f"Patient said: \"{transcript}\""

    messages: list[dict[str, str]] = [
        *FEW_SHOT_MESSAGES,
        {"role": "user", "content": user_msg},
    ]

    try:
        resp = await client.messages.create(
            model=settings.anthropic_model,
            max_tokens=512,
            system=SYSTEM_PROMPT,
            messages=messages,
        )
        text: str = resp.content[0].text  # type: ignore[union-attr]
        data = _extract_json(text)
        return TriageResult(**data)
    except Exception:
        logger.exception("Claude triage call failed — falling back to mock")
        if settings.allow_mocks:
            return _mock_triage(transcript)
        raise
