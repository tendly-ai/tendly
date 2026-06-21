"""Memory layer (§3.5).

Redis-backed implementation with automatic fallback to in-memory dicts when
REDIS_URL is missing or the connection fails. The module-level function
signatures are the public contract — every other module imports these.

Key schema
----------
Long-term (no TTL):
    patient:{patient_id}       — JSON of PatientProfile
    caregiver:{caregiver_id}   — JSON of CaregiverProfile
    request:{request_id}       — JSON of CareRequest
    tendly:request_ids         — Redis SET of all request_id strings

Short-term / session (§3.5.1, 1-hour TTL):
    session:{patient_id}:transcript_history — Redis LIST (most-recent at right)
    session:{patient_id}:recent_requests    — Redis LIST
    session:{patient_id}:current_task       — plain string
"""
from __future__ import annotations

import logging
from typing import Optional

from ..config import get_settings
from ..models import (
    CareRequest,
    CaregiverProfile,
    PatientProfile,
    Status,
    URGENCY_RANK,
)

logger = logging.getLogger("tendly.memory")

SESSION_TTL_SECONDS = 3600  # 1 hour

# ---------------------------------------------------------------------------
# In-memory fallback stores (used when Redis is unavailable)
# ---------------------------------------------------------------------------
_patients: dict[str, PatientProfile] = {}
_caregivers: dict[str, CaregiverProfile] = {}
_requests: dict[str, CareRequest] = {}
_transcripts: dict[str, list[str]] = {}

# ---------------------------------------------------------------------------
# Redis connection helper
# ---------------------------------------------------------------------------
_redis_client = None  # will be a redis.Redis instance or None
_use_redis: bool = False

settings = get_settings()


def _init_redis():
    """Attempt to connect to Redis. Called once at import time."""
    global _redis_client, _use_redis
    redis_url = settings.redis_url
    if not redis_url:
        logger.info("REDIS_URL not set — using in-memory fallback")
        return
    if not (redis_url.startswith("redis://") or redis_url.startswith("rediss://") or redis_url.startswith("unix://")):
        logger.warning("REDIS_URL does not specify a valid scheme (redis://, rediss://, or unix://) — using in-memory fallback")
        return
    try:
        import redis as _redis_lib
        client = _redis_lib.Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
        client.ping()
        _redis_client = client
        _use_redis = True
        logger.info("Connected to Redis successfully")
    except Exception:
        logger.warning("Redis connection failed — falling back to in-memory", exc_info=True)


_init_redis()


def _safe_redis(fn, *args, **kwargs):
    """Run *fn* against Redis; on any error, log and return None."""
    global _use_redis
    try:
        return fn(*args, **kwargs)
    except Exception:
        logger.warning("Redis operation failed", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Patients
# ---------------------------------------------------------------------------
def save_patient(profile: PatientProfile) -> None:
    if _use_redis:
        _safe_redis(
            _redis_client.set,
            f"patient:{profile.patient_id}",
            profile.model_dump_json(),
        )
    _patients[profile.patient_id] = profile


def get_patient(patient_id: str) -> Optional[PatientProfile]:
    if _use_redis:
        raw = _safe_redis(_redis_client.get, f"patient:{patient_id}")
        if raw is not None:
            return PatientProfile.model_validate_json(raw)
        return None
    return _patients.get(patient_id)


def list_patients() -> list[PatientProfile]:
    if _use_redis:
        keys = _safe_redis(_redis_client.keys, "patient:*")
        if keys:
            results: list[PatientProfile] = []
            for k in keys:
                raw = _safe_redis(_redis_client.get, k)
                if raw:
                    results.append(PatientProfile.model_validate_json(raw))
            return results
        return []
    return list(_patients.values())


# ---------------------------------------------------------------------------
# Caregivers
# ---------------------------------------------------------------------------
def save_caregiver(profile: CaregiverProfile) -> None:
    if _use_redis:
        _safe_redis(
            _redis_client.set,
            f"caregiver:{profile.caregiver_id}",
            profile.model_dump_json(),
        )
    _caregivers[profile.caregiver_id] = profile


def get_caregiver(caregiver_id: str) -> Optional[CaregiverProfile]:
    if _use_redis:
        raw = _safe_redis(_redis_client.get, f"caregiver:{caregiver_id}")
        if raw is not None:
            return CaregiverProfile.model_validate_json(raw)
        return None
    return _caregivers.get(caregiver_id)


def list_caregivers() -> list[CaregiverProfile]:
    if _use_redis:
        keys = _safe_redis(_redis_client.keys, "caregiver:*")
        if keys:
            results: list[CaregiverProfile] = []
            for k in keys:
                raw = _safe_redis(_redis_client.get, k)
                if raw:
                    results.append(CaregiverProfile.model_validate_json(raw))
            return results
        return []
    return list(_caregivers.values())


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------
def save_request(req: CareRequest) -> None:
    if _use_redis:
        _safe_redis(
            _redis_client.set,
            f"request:{req.request_id}",
            req.model_dump_json(),
        )
        _safe_redis(_redis_client.sadd, "tendly:request_ids", req.request_id)
    _requests[req.request_id] = req


def get_request(request_id: str) -> Optional[CareRequest]:
    if _use_redis:
        raw = _safe_redis(_redis_client.get, f"request:{request_id}")
        if raw is not None:
            return CareRequest.model_validate_json(raw)
        return None
    return _requests.get(request_id)


def update_request_status(request_id: str, status: Status) -> Optional[CareRequest]:
    req = get_request(request_id)
    if req is None:
        return None
    req.status = status
    save_request(req)
    return req


def _sort_requests(reqs: list[CareRequest]) -> list[CareRequest]:
    """Active requests sorted by urgency, then oldest-first. Resolved last."""
    def key(r: CareRequest):
        resolved = 1 if r.status == Status.resolved else 0
        return (resolved, URGENCY_RANK[r.urgency], r.created_at)
    return sorted(reqs, key=key)


def list_requests() -> list[CareRequest]:
    """Active requests sorted by urgency, then oldest-first within a tier.

    Resolved requests sink to the bottom (§3.2.3).
    """
    if _use_redis:
        ids = _safe_redis(_redis_client.smembers, "tendly:request_ids")
        if ids:
            reqs: list[CareRequest] = []
            for rid in ids:
                raw = _safe_redis(_redis_client.get, f"request:{rid}")
                if raw:
                    reqs.append(CareRequest.model_validate_json(raw))
            return _sort_requests(reqs)
        return []
    return _sort_requests(list(_requests.values()))


def get_requests_for_patient(patient_id: str) -> list[CareRequest]:
    return [r for r in list_requests() if r.patient_id == patient_id]


# ---------------------------------------------------------------------------
# Short-term session memory (§3.5.1)
# ---------------------------------------------------------------------------
def append_transcript(patient_id: str, transcript: str) -> None:
    key = f"session:{patient_id}:transcript_history"
    if _use_redis:
        _safe_redis(_redis_client.rpush, key, transcript)
        _safe_redis(_redis_client.expire, key, SESSION_TTL_SECONDS)
    _transcripts.setdefault(patient_id, []).append(transcript)


def get_recent_transcripts(patient_id: str, limit: int = 10) -> list[str]:
    key = f"session:{patient_id}:transcript_history"
    if _use_redis:
        result = _safe_redis(_redis_client.lrange, key, -limit, -1)
        if result is not None:
            return list(result)
        return []
    return _transcripts.get(patient_id, [])[-limit:]


# ---------------------------------------------------------------------------
# Derived context for triage + dashboard
# ---------------------------------------------------------------------------
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
