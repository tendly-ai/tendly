"""Patient + caregiver profile endpoints (§4)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..services import memory

router = APIRouter(prefix="/api", tags=["patients"])


@router.get("/patients")
async def list_patients():
    return [p.model_dump() for p in memory.list_patients()]


@router.get("/patients/{patient_id}")
async def get_patient(patient_id: str):
    p = memory.get_patient(patient_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    return p.model_dump()


@router.get("/caregivers")
async def list_caregivers():
    return [c.model_dump() for c in memory.list_caregivers()]
