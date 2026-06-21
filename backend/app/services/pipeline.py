"""Shared care-request pipeline (§3.3–§3.4).

`process_care_request` is the single code path used by both the HTTP route
(`routes/requests.py`) and the conversational voice agent. It enriches a raw
transcript with patient context, runs Claude triage, routes automatable
categories, persists the result, and broadcasts it to the dashboard.
"""
from __future__ import annotations

import uuid

from fastapi import HTTPException

from ..models import CareRequest, Category
from ..ws import manager
from . import automation, memory, observability, talkback, triage


async def process_care_request(patient_id: str, transcript: str) -> CareRequest:
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
