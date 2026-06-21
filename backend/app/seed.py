"""Seed mock data: 3 patients + 2 caregivers (§5)."""
from __future__ import annotations

from .models import CaregiverProfile, FamilyContact, PatientProfile
from .services import memory

PATIENTS = [
    PatientProfile(
        patient_id="patient_001",
        name="Mary Johnson",
        room_number="204B",
        age=82,
        preferred_language="English",
        family_contacts=[FamilyContact(name="Sarah Johnson", relation="daughter",
                                       phone="555-0123", email="sarah@example.com")],
        caregiver_contacts=["nurse_jenny", "aide_carlos"],
        common_requests=["water", "TV help", "call daughter"],
        interests=["Lakers basketball", "old movies", "gardening"],
        routine_preferences="Likes to wake up early. Prefers warm drinks in the afternoon.",
        notes="Hard of hearing in left ear. Prefers being called 'Mary' not 'Mrs. Johnson'.",
    ),
    PatientProfile(
        patient_id="patient_002",
        name="Robert Chen",
        room_number="118A",
        age=79,
        preferred_language="English",
        family_contacts=[FamilyContact(name="David Chen", relation="son",
                                       phone="555-0144", email="david@example.com")],
        caregiver_contacts=["nurse_jenny"],
        common_requests=["crossword help", "water", "reading light"],
        interests=["crossword puzzles", "engineering", "jazz"],
        routine_preferences="Enjoys quiet mornings. Likes the news at noon.",
        notes="Former engineer. Independent; appreciates clear explanations.",
    ),
    PatientProfile(
        patient_id="patient_003",
        name="Gloria Reyes",
        room_number="305",
        age=88,
        preferred_language="English",
        family_contacts=[FamilyContact(name="Sofia Reyes", relation="granddaughter",
                                       phone="555-0199", email="sofia@example.com")],
        caregiver_contacts=["aide_carlos"],
        common_requests=["music", "call Sofia", "blanket"],
        interests=["music", "dancing", "cooking"],
        routine_preferences="Loves music in the evenings.",
        notes="Speaks English and Spanish. Warm and social.",
    ),
]

CAREGIVERS = [
    CaregiverProfile(
        caregiver_id="nurse_jenny",
        name="Jenny Park",
        role="Registered Nurse",
        assigned_patients=["patient_001", "patient_002", "patient_003"],
    ),
    CaregiverProfile(
        caregiver_id="aide_carlos",
        name="Carlos Mendez",
        role="Nursing Aide",
        assigned_patients=["patient_001", "patient_003"],
    ),
]


def seed() -> None:
    for p in PATIENTS:
        memory.save_patient(p)
    for c in CAREGIVERS:
        memory.save_caregiver(c)
