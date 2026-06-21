"""Automated task confirmation endpoint (§3.4.3, §4)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..models import ConfirmTaskBody, Status
from ..services import automation, memory, talkback
from ..ws import manager

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.post("/confirm")
async def confirm(body: ConfirmTaskBody):
    req = memory.get_request(body.request_id)
    if req is None:
        raise HTTPException(status_code=404, detail="Request not found")

    if not body.confirmed:
        req.task_state = "cancelled"
        req.status = Status.resolved
        req.spoken_response = await talkback.cancelled(req)
        memory.save_request(req)
        await manager.broadcast("request.updated", req.model_dump())
        return {
            "status": "cancelled",
            "request_id": req.request_id,
            "spoken_response": req.spoken_response,
        }

    result = await automation.run_task(req)
    req.task_state = result.get("status", "done")
    if req.task_state in ("done", "mocked"):
        req.status = Status.resolved
    req.spoken_response = await talkback.automation_done(req)
    memory.save_request(req)
    await manager.broadcast("request.updated", req.model_dump())
    return {
        "status": req.task_state,
        "detail": result.get("detail"),
        "request_id": req.request_id,
        "spoken_response": req.spoken_response,
    }
