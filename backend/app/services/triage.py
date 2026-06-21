"""AI triage via Claude (§3.3).

CONTRACT: `triage(transcript, patient_context) -> TriageResult`. The AI-triage
feature subagent implements the real Claude call with the safety system prompt
and few-shot examples from §3.3.5, and wraps it with Arize logging
(observability.log_triage). This scaffold ships a deterministic keyword-based
mock so the dashboard shows realistic cards before Claude is wired up.
"""
from __future__ import annotations

from ..config import get_settings
from ..models import Category, TriageResult, Urgency

settings = get_settings()

SYSTEM_PROMPT = """You are a triage assistant in an elderly care facility. \
You classify patient voice requests so caregivers can prioritize them.

SAFETY RULES (non-negotiable):
1. You are NOT a doctor. Never diagnose, prescribe, or give medical advice.
2. When in doubt, escalate. If a request is medically ambiguous or the patient \
sounds distressed, classify as urgent_medical with high or emergency urgency. \
False positives are acceptable; false negatives are not.
3. Never suppress a request. Every utterance produces a record, even chit-chat.
4. Confirm before sensitive automation (sending data, submitting forms, \
accessing private info): set requires_confirmation=true.

Return ONLY JSON with keys: category, urgency, summary, suggested_action, \
requires_confirmation, reasoning.
category is one of: urgent_medical, in_person_caregiver, routine_comfort, \
automated_task, family_communication, general_conversation.
urgency is one of: emergency, high, medium, low."""


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


async def triage(transcript: str, patient_context: str = "") -> TriageResult:
    """Classify a patient transcript into a TriageResult."""
    if not settings.anthropic_api_key:
        if settings.allow_mocks:
            return _mock_triage(transcript)
        raise RuntimeError("ANTHROPIC_API_KEY not configured")

    # Real implementation (Claude). Implemented by the AI-triage feature.
    import json

    from anthropic import AsyncAnthropic  # type: ignore

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    user_msg = transcript
    if patient_context:
        user_msg = f"Patient context:\n{patient_context}\n\nPatient said: {transcript}"
    resp = await client.messages.create(
        model=settings.anthropic_model,
        max_tokens=512,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    text = resp.content[0].text  # type: ignore[attr-defined]
    start, end = text.find("{"), text.rfind("}")
    data = json.loads(text[start : end + 1])
    return TriageResult(**data)
