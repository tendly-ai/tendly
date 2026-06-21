"""Family summary generation via Claude (§3.6).

CONTRACT: `generate_summary(patient_id, start_date, end_date) -> str` returns a
warm, non-clinical weekly summary suitable for sharing with a patient's family.

The function uses Claude to produce a WARM, simple, non-clinical weekly update
including (§3.6.2):
  - General well-being
  - Common requests this period
  - Gentle notes on changes or concerns (no alarming language)
  - Interests and activities the patient mentioned
  - Suggested talking points for the next family visit

NON-SENSITIVE only — no raw medical data, no verbatim distressing transcripts.
Falls back to a deterministic mock when ANTHROPIC_API_KEY is missing.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from ..config import get_settings
from . import memory

logger = logging.getLogger(__name__)
settings = get_settings()

SYSTEM_PROMPT = """\
You are writing a warm, simple weekly update for the family of an elderly care \
resident. Your audience is a loving family member who wants to know their \
relative is happy and cared for.

RULES:
- Use plain, reassuring, conversational language — as if a friendly nurse were \
  chatting with the family over coffee.
- NEVER include raw medical terminology, clinical data, medication names, or \
  verbatim distressing quotes.
- Do NOT use alarming language even if a concern exists; frame gently.
- Keep it to 1–2 short paragraphs (roughly 100–180 words).

STRUCTURE (weave naturally, don't use headers):
1. General well-being and mood this week.
2. Common things they asked for or enjoyed.
3. Any gentle observations about changes (frame positively or neutrally).
4. Interests, hobbies, or topics they brought up.
5. End with 1–2 suggested talking points for the family's next visit.
"""


def _mock_summary(patient_id: str) -> str:
    """Deterministic fallback when no API key is configured."""
    p = memory.get_patient(patient_id)
    if p is None:
        return "No data available for this patient."
    reqs = memory.get_requests_for_patient(patient_id)
    interests = ", ".join(p.interests) if p.interests else "their usual hobbies"
    common = ", ".join(p.common_requests) if p.common_requests else "a few small things"
    return (
        f"{p.name} had a calm week. They asked for help with {common} a few times "
        f"and enjoyed talking about {interests}. Nothing concerning to report. "
        f"A nice thing to bring up on your next visit would be {interests.split(',')[0]}. "
        f"({len(reqs)} requests logged this period.)"
    )


def _filter_requests_by_date(
    reqs: list,
    start_date: Optional[str],
    end_date: Optional[str],
) -> list:
    """Filter requests by optional ISO date strings."""
    if not start_date and not end_date:
        return reqs
    filtered = reqs
    if start_date:
        try:
            start_dt = datetime.fromisoformat(start_date)
            filtered = [r for r in filtered if r.created_at >= start_dt]
        except ValueError:
            pass
    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date)
            filtered = [r for r in filtered if r.created_at <= end_dt]
        except ValueError:
            pass
    return filtered


async def generate_summary(
    patient_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> str:
    """Generate a family-friendly summary for a patient.

    Uses Claude when ANTHROPIC_API_KEY is set; falls back to a deterministic
    mock otherwise (or if the Claude call fails at runtime).
    """
    if not settings.anthropic_api_key:
        if settings.allow_mocks:
            return _mock_summary(patient_id)
        raise RuntimeError("ANTHROPIC_API_KEY not configured")

    from anthropic import AsyncAnthropic  # type: ignore

    p = memory.get_patient(patient_id)
    if p is None:
        return "No data available for this patient."

    reqs = memory.get_requests_for_patient(patient_id)
    reqs = _filter_requests_by_date(reqs, start_date, end_date)
    context = memory.get_patient_context(patient_id)

    # Build a concise request history for the prompt
    if reqs:
        history_lines = [f"- {r.summary} (category: {r.category.value})" for r in reqs]
        history = "\n".join(history_lines)
    else:
        history = "(No requests logged this period — a quiet week.)"

    date_range = ""
    if start_date or end_date:
        date_range = f" (period: {start_date or 'beginning'} to {end_date or 'now'})"

    user_message = (
        f"Patient profile:\n{context}\n\n"
        f"Requests this period{date_range}:\n{history}\n\n"
        f"Write the weekly family update for {p.name}."
    )

    try:
        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        resp = await client.messages.create(
            model=settings.anthropic_model,
            max_tokens=512,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        return resp.content[0].text  # type: ignore[attr-defined]
    except Exception:
        logger.exception("Claude summary generation failed; falling back to mock")
        if settings.allow_mocks:
            return _mock_summary(patient_id)
        raise
