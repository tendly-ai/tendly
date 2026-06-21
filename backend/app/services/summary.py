"""Family summary generation via Claude (§3.6).

CONTRACT: `generate_summary(patient_id, start_date, end_date) -> str` returns a
warm, non-clinical weekly summary. The family-summary feature subagent
implements the real Claude prompt; this scaffold builds a deterministic summary
from memory so the endpoint works end-to-end.
"""
from __future__ import annotations

from typing import Optional

from ..config import get_settings
from . import memory

settings = get_settings()

SYSTEM_PROMPT = """You write warm, simple, non-clinical weekly updates for the \
family of an elderly care resident. Use plain, reassuring language. Include: \
general well-being, common requests, gentle notes on any changes, interests the \
patient mentioned, and suggested talking points for the next visit. Never \
include raw medical data or verbatim distressing transcripts."""


def _mock_summary(patient_id: str) -> str:
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


async def generate_summary(
    patient_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> str:
    """Generate a family-friendly summary for a patient."""
    if not settings.anthropic_api_key:
        if settings.allow_mocks:
            return _mock_summary(patient_id)
        raise RuntimeError("ANTHROPIC_API_KEY not configured")

    # Real implementation (Claude). Implemented by the family-summary feature.
    from anthropic import AsyncAnthropic  # type: ignore

    p = memory.get_patient(patient_id)
    reqs = memory.get_requests_for_patient(patient_id)
    if p is None:
        return "No data available for this patient."
    context = memory.get_patient_context(patient_id)
    history = "\n".join(f"- {r.summary} ({r.category})" for r in reqs)
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    resp = await client.messages.create(
        model=settings.anthropic_model,
        max_tokens=512,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"Patient profile:\n{context}\n\nThis period's requests:\n{history}\n\n"
                       f"Write the weekly family update for {p.name}.",
        }],
    )
    return resp.content[0].text  # type: ignore[attr-defined]
