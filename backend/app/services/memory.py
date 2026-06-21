"""Memory layer (§3.5).

CONTRACT — the rest of the app depends on these module-level functions. The
Redis-memory feature subagent should implement them backed by Redis (short-term
keys `session:{patient_id}:*` with TTL, long-term keys `patient:{id}` /
`caregiver:{id}`) while keeping the SAME signatures. This scaffold ships an
in-memory fallback so the app runs end-to-end before Redis is wired up.
"""
from __future__ import annotations

from typing import Optional

from ..config import get_settings
from ..models import (
    CareRequest,
    CaregiverProfile,
    PatientProfile,
    Status,
    URGENCY_RANK,
)

# ---- In-memory fallback stores (replaced/backed by Redis by the memory feature) ----
_patients: dict[str, PatientProfile] = {}
_caregivers: dict[str, CaregiverProfile] = {}
_requests: dict[str, CareRequest] = {}
_transcripts: dict[str, list[str]] = {}

settings = get_settings()


# ---------- Patients ----------
def save_patient(profile: PatientProfile) -> None:
    _patients[profile.patient_id] = profile


def get_patient(patient_id: str) -> Optional[PatientProfile]:
    return _patients.get(patient_id)


def list_patients() -> list[PatientProfile]:
    return list(_patients.values())


# ---------- Caregivers ----------
def save_caregiver(profile: CaregiverProfile) -> None:
    _caregivers[profile.caregiver_id] = profile


def get_caregiver(caregiver_id: str) -> Optional[CaregiverProfile]:
    return _caregivers.get(caregiver_id)


def list_caregivers() -> list[CaregiverProfile]:
    return list(_caregivers.values())


# ---------- Requests ----------
def save_request(req: CareRequest) -> None:
    _requests[req.request_id] = req


def get_request(request_id: str) -> Optional[CareRequest]:
    return _requests.get(request_id)


def update_request_status(request_id: str, status: Status) -> Optional[CareRequest]:
    req = _requests.get(request_id)
    if req is None:
        return None
    req.status = status
    _requests[request_id] = req
    return req


def list_requests() -> list[CareRequest]:
    """Active requests sorted by urgency, then oldest-first within a tier.

    Resolved requests sink to the bottom (§3.2.3).
    """
    def key(r: CareRequest):
        resolved = 1 if r.status == Status.resolved else 0
        return (resolved, URGENCY_RANK[r.urgency], r.created_at)

    return sorted(_requests.values(), key=key)


def get_requests_for_patient(patient_id: str) -> list[CareRequest]:
    return [r for r in _requests.values() if r.patient_id == patient_id]


# ---------- Short-term session memory ----------
def append_transcript(patient_id: str, transcript: str) -> None:
    _transcripts.setdefault(patient_id, []).append(transcript)


def get_recent_transcripts(patient_id: str, limit: int = 10) -> list[str]:
    return _transcripts.get(patient_id, [])[-limit:]


# ---------- Derived context for triage + dashboard ----------
def get_patient_context(patient_id: str) -> str:
    """Plain-text context block used by triage prompts and dashboard cards."""
    p = get_patient(patient_id)
    if p is None:
        return ""
    parts = [
        f"Name: {p.name}",
        f"Room: {p.room_number}",
    ]
    if p.age:
        parts.append(f"Age: {p.age}")
    if p.common_requests:
        parts.append("Common requests: " + ", ".join(p.common_requests))
    if p.interests:
        parts.append("Interests: " + ", ".join(p.interests))
    if p.routine_preferences:
        parts.append("Preferences: " + p.routine_preferences)
    if p.notes:
        parts.append("Notes: " + p.notes)
    recent = get_recent_transcripts(patient_id, limit=5)
    if recent:
        parts.append("Recent things said: " + " | ".join(recent))
    return "\n".join(parts)
