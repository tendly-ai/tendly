"""Speech synthesis endpoint for patient talk-back responses."""
from __future__ import annotations

from io import BytesIO

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ..models import SpeechBody
from ..services import stt

router = APIRouter(prefix="/api/speech", tags=["speech"])


@router.post("/synthesize")
async def synthesize(body: SpeechBody):
    text = " ".join(body.text.split())
    if not text:
        raise HTTPException(status_code=422, detail="text is required")

    audio = await stt.synthesize(text[:600])
    if not audio:
        raise HTTPException(status_code=503, detail="Speech synthesis unavailable")

    return StreamingResponse(BytesIO(audio), media_type="audio/mpeg")
