"""Patient request endpoints (§4). The core triage pipeline lives in
`services/pipeline.py` so the voice agent can reuse it."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..models import UpdateStatusBody
from ..services import memory, stt
from ..services.pipeline import process_care_request
from ..ws import manager

router = APIRouter(prefix="/api/requests", tags=["requests"])


@router.post("")
async def create_request(request: Request):
    """Create a request from JSON {patient_id, transcript} or multipart audio.

    JSON: {"patient_id": "...", "transcript": "..."}
    Multipart form: patient_id=<id>, audio=<file>  (audio is transcribed via STT)
    """
    content_type = request.headers.get("content-type", "")
    patient_id: str | None = None
    transcript: str | None = None

    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        patient_id = form.get("patient_id")  # type: ignore[assignment]
        transcript = form.get("transcript")  # type: ignore[assignment]
        audio = form.get("audio")
        if not transcript and audio is not None and hasattr(audio, "read"):
            audio_bytes = await audio.read()  # type: ignore[union-attr]
            transcript = await stt.transcribe(
                audio_bytes, getattr(audio, "content_type", "audio/webm") or "audio/webm"
            )
    else:
        data = await request.json()
        patient_id = data.get("patient_id")
        transcript = data.get("transcript")

    if not patient_id:
        raise HTTPException(status_code=422, detail="patient_id is required")
    if not transcript:
        raise HTTPException(status_code=422, detail="transcript or audio is required")

    req = await process_care_request(patient_id, transcript)
    return req.model_dump()


@router.get("")
async def list_requests():
    return [r.model_dump() for r in memory.list_requests()]


@router.get("/{request_id}")
async def get_request(request_id: str):
    req = memory.get_request(request_id)
    if req is None:
        raise HTTPException(status_code=404, detail="Request not found")
    return req.model_dump()


@router.patch("/{request_id}")
async def update_status(request_id: str, body: UpdateStatusBody):
    req = memory.update_request_status(request_id, body.status)
    if req is None:
        raise HTTPException(status_code=404, detail="Request not found")
    await manager.broadcast("request.updated", req.model_dump())
    return req.model_dump()
