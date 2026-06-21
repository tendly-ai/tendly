"""Contact lookup endpoint — resolves a name to email/phone via macOS Contacts.

Used by the caregiver progress-report flow so a report can be addressed to a
person who isn't already in the patient's seeded family contacts.
"""
from __future__ import annotations

from fastapi import APIRouter

from ..services import automation

router = APIRouter(prefix="/api/contacts", tags=["contacts"])


@router.get("/lookup")
async def lookup(name: str):
    """Look up a single contact by name. Returns {} when nothing is found."""
    name = (name or "").strip()
    if not name:
        return {}
    contact = automation._lookup_macos_contact(name)
    if contact is None:
        return {}
    return {
        "name": contact.name,
        "email": contact.email,
        "phone": contact.phone,
    }
