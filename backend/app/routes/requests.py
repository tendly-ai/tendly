"""Patient request endpoints (§4). Core triage pipeline lives here."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request

from ..models import (
    CareRequest,
    Category,
    Status,
    UpdateStatusBody,
)
from ..services import automation, memory, observability, stt, talkback, triage
from ..ws import manager

router = APIRouter(prefix="/api/requests", tags=["requests"])


async def _process(patient_id: str, transcript: str) -> CareRequest:
    """Run the full pipeline: enrich -> triage -> persist -> broadcast."""
    patient = memory.get_patient(patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail=f"Unknown patient {patient_id}")

    memory.append_transcript(patient_id, transcript)
    context = memory.get_patient_context(patient_id)

    with observability.Timer() as timer:
        result = await triage.triage(transcript, context)
    observability.log_triage(
        transcript=transcript, patient_context=context, result=result,
        latency_ms=getattr(timer, "elapsed_ms", 0.0), patient_id=patient_id,
    )

    req = CareRequest(
        request_id=f"req_{uuid.uuid4().hex[:8]}",
        patient_id=patient_id,
        patient_name=patient.name,
        room_number=patient.room_number,
        transcript=transcript,
        category=result.category,
        urgency=result.urgency,
        summary=result.summary,
        suggested_action=result.suggested_action,
        requires_confirmation=result.requires_confirmation,
        patient_context=context,
    )

    # Automation routing (§3.4): automatable categories.
    if result.category in (Category.automated_task, Category.family_communication):
        task_plan = automation.plan_task(req)
        req.confirmation_prompt = (
            await talkback.confirmation(req, task_plan)
            if result.requires_confirmation else None
        )
        if result.requires_confirmation:
            req.task_state = "pending_confirmation"
            req.spoken_response = req.confirmation_prompt
        else:
            res = await automation.run_task(req)
            req.task_state = res.get("status", "done")
            req.status = Status.resolved
            req.spoken_response = (
                await talkback.automation_done(req)
                if req.task_state in ("done", "mocked")
                else await talkback.automation_tried(req)
            )
    else:
        req.spoken_response = await talkback.caregiver_notified(req)

    memory.save_request(req)
    await manager.broadcast("request.created", req.model_dump())
    return req


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

    req = await _process(patient_id, transcript)
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
