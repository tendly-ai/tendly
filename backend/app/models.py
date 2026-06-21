"""Shared data models — the integration contract for all Tendly features.

Feature subagents MUST keep these schemas stable. Add fields if needed, but do
not rename or remove existing ones without coordinating, since the frontend and
other services depend on them.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Category(str, Enum):
    urgent_medical = "urgent_medical"
    in_person_caregiver = "in_person_caregiver"
    routine_comfort = "routine_comfort"
    automated_task = "automated_task"
    family_communication = "family_communication"
    general_conversation = "general_conversation"


class Urgency(str, Enum):
    emergency = "emergency"
    high = "high"
    medium = "medium"
    low = "low"


class Status(str, Enum):
    new = "new"
    in_progress = "in_progress"
    resolved = "resolved"


# Numeric rank for sorting (lower = more urgent).
URGENCY_RANK = {
    Urgency.emergency: 0,
    Urgency.high: 1,
    Urgency.medium: 2,
    Urgency.low: 3,
}


class FamilyContact(BaseModel):
    name: str
    relation: str
    phone: Optional[str] = None
    email: Optional[str] = None


class PatientProfile(BaseModel):
    patient_id: str
    name: str
    room_number: str
    age: Optional[int] = None
    preferred_language: str = "English"
    family_contacts: list[FamilyContact] = Field(default_factory=list)
    caregiver_contacts: list[str] = Field(default_factory=list)
    common_requests: list[str] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)
    routine_preferences: str = ""
    notes: str = ""


class CaregiverProfile(BaseModel):
    caregiver_id: str
    name: str
    role: str
    assigned_patients: list[str] = Field(default_factory=list)
    active_requests: list[str] = Field(default_factory=list)


class TriageResult(BaseModel):
    """Structured output of the Claude triage step (§3.3.4)."""
    category: Category
    urgency: Urgency
    summary: str
    suggested_action: str
    requires_confirmation: bool = False
    reasoning: str = ""  # for Arize logging, not shown to patients


class CareRequest(BaseModel):
    """A single patient request as shown on the caregiver dashboard (§3.2.2)."""
    request_id: str
    patient_id: str
    patient_name: str
    room_number: str
    transcript: str
    category: Category
    urgency: Urgency
    summary: str
    suggested_action: str
    requires_confirmation: bool = False
    status: Status = Status.new
    created_at: datetime = Field(default_factory=utcnow)
    patient_context: str = ""
    # Automation bookkeeping (used by Simular flow)
    confirmation_prompt: Optional[str] = None
    task_state: Optional[str] = None  # e.g. pending_confirmation, running, done, cancelled


# ---- API request/response bodies ----

class CreateRequestBody(BaseModel):
    patient_id: str
    transcript: Optional[str] = None  # if omitted, audio must be provided


class UpdateStatusBody(BaseModel):
    status: Status


class ConfirmTaskBody(BaseModel):
    request_id: str
    confirmed: bool


class GenerateSummaryBody(BaseModel):
    patient_id: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
