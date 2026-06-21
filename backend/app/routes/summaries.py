"""Family summary endpoint (§3.6, §4)."""
from __future__ import annotations

from fastapi import APIRouter

from ..models import GenerateSummaryBody
from ..services import summary

router = APIRouter(prefix="/api/summaries", tags=["summaries"])


@router.post("/generate")
async def generate(body: GenerateSummaryBody):
    text = await summary.generate_summary(body.patient_id, body.start_date, body.end_date)
    return {"patient_id": body.patient_id, "summary": text}
