"""Computer automation via Simular `simulang` (§3.4).

CONTRACT: `run_task(req) -> dict` executes a digital task for the patient and
returns {status, detail}. `plan_task(req) -> str` returns a human-readable
description of the action to be taken (used for the patient confirmation prompt).

Integration strategy:
- Generates TypeScript scripts targeting `@simular-ai/simulang-js` (the real
  desktop-automation library: browser opening, accessibility-tree checks,
  video calls, messaging, etc.)
- Attempts execution via the `simulang run` CLI subprocess.
- Falls back to a rich mock if simulang is unavailable (e.g. headless env,
  no Node 22.18+, no macOS permissions). Guarded by TENDLY_ALLOW_MOCKS.

Real vs Mocked:
- plan_task: always real (pure logic, no external calls)
- _generate_script: always real (produces valid simulang-js TypeScript)
- _execute_simulang: real attempt via subprocess; falls back to mock
- run_task: orchestrates the above; returns mock detail when subprocess fails
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus, urlparse

from ..config import get_settings
from ..models import CareRequest, Category, FamilyContact, PatientProfile

logger = logging.getLogger(__name__)

settings = get_settings()

_MESSAGE_BODY_STARTERS = {
    "hello", "hi", "hey", "good", "please", "thanks", "thank",
    "i", "i'm", "im", "we", "can", "could", "would", "will",
    "want", "miss", "love", "need", "hope", "see", "call",
}

# ---------------------------------------------------------------------------
# Task type detection
# ---------------------------------------------------------------------------

_TASK_OPEN_EMAIL = "open_email"
_TASK_OPEN_WEBSITE = "open_website"
_TASK_PLAY_MEDIA = "play_media"
_TASK_VIDEO_CALL = "video_call"
_TASK_SEND_MESSAGE = "send_message"
_TASK_OPEN_PORTAL = "open_portal"
_TASK_GENERAL_NAV = "general_navigation"


@dataclass
class AutomationPlan:
    action: str
    target_url: str
    description: str
    search_query: str = ""
    site: str = ""


@dataclass
class MessageIntent:
    recipient_name: str
    body: str


_KNOWN_SITE_URLS = {
    "amazon": "https://www.amazon.com",
    "cnn": "https://www.cnn.com",
    "facebook": "https://www.facebook.com",
    "gmail": "https://mail.google.com",
    "google": "https://www.google.com",
    "instagram": "https://www.instagram.com",
    "linkedin": "https://www.linkedin.com",
    "mail": "https://mail.google.com",
    "mychart": "https://www.mychart.org",
    "netflix": "https://www.netflix.com",
    "nytimes": "https://www.nytimes.com",
    "spotify": "https://open.spotify.com",
    "weather": "https://weather.com",
    "wikipedia": "https://www.wikipedia.org",
    "youtube": "https://www.youtube.com",
}


MESSAGE_INTENT_PROMPT = """\
Extract the intended recipient and message body from an elderly patient's
spoken request.

Return ONLY valid JSON:
{
  "recipient_name": "person name or relationship, no extra words",
  "body": "exact message the patient wants drafted"
}

Rules:
- Do not include command words such as send, text, message, tell, saying, or that.
- If the patient says "tell Henry I want to see you soon", recipient_name is
  "Henry" and body is "I want to see you soon".
- Preserve the patient's message content, but remove filler such as
  "in my messages", "for me", and "please send".
- If no explicit body exists, return an empty body.
- Never invent facts or contacts.
"""


AUTOMATION_PLANNER_PROMPT = """\
You convert an elderly patient's digital request into one safe browser action.

Return ONLY valid JSON with these keys:
{
  "action": "open_url" | "search_web" | "site_search",
  "target_url": "https://...",
  "search_query": "plain search query or empty string",
  "site": "site name or empty string",
  "description": "short first-person-friendly action description"
}

Rules:
- For "open <known site/app>" requests, choose the official public URL, e.g.
  LinkedIn -> https://www.linkedin.com.
- For research/discovery requests like recipes, news, weather, or "find me...",
  use action "search_web" with a concise query and target_url set to a Google
  search URL for that query.
- For requests like "go to LinkedIn and search Jane Smith", use action
  "site_search", site "linkedin", search_query "Jane Smith", and target_url set
  to that site's search-results URL. Prefer native site search URLs for
  LinkedIn, YouTube, Amazon, Wikipedia, Google, Gmail, and Instagram when you
  know them; otherwise use a Google site: search.
- Do not invent private account URLs, click buttons, submit forms, buy things,
  send messages, or enter personal data. If the request would need that, open
  the relevant public site or search page only.
- Use HTTPS URLs only, except mailto is allowed for explicit email compose tasks.
"""


def _classify_task(req: CareRequest) -> str:
    """Determine the specific automation task type from category + transcript."""
    t = req.transcript.lower()

    # Family communication category always maps to call/message
    if req.category == Category.family_communication:
        if any(kw in t for kw in ("message", "text", "send", "tell")):
            return _TASK_SEND_MESSAGE
        if any(kw in t for kw in ("call", "video", "facetime", "zoom")):
            return _TASK_VIDEO_CALL
        return _TASK_SEND_MESSAGE

    # Automated task category — match by keywords in transcript
    if "email" in t or "mail" in t or "inbox" in t:
        return _TASK_OPEN_EMAIL
    if "portal" in t or "patient portal" in t or "health record" in t:
        return _TASK_OPEN_PORTAL
    if any(kw in t for kw in ("music", "play", "song", "spotify", "radio", "show", "netflix", "youtube")):
        return _TASK_PLAY_MEDIA
    if any(kw in t for kw in ("call", "video", "facetime", "zoom")):
        return _TASK_VIDEO_CALL
    if any(kw in t for kw in ("message", "text", "send")):
        return _TASK_SEND_MESSAGE
    if any(kw in t for kw in ("browse", "website", "internet", "search", "google", "open", "go to", "linkedin")):
        return _TASK_OPEN_WEBSITE

    return _TASK_GENERAL_NAV


# ---------------------------------------------------------------------------
# plan_task — human-readable plan (always real, no external deps)
# ---------------------------------------------------------------------------


def _get_family_contact(patient_id: str) -> Optional[FamilyContact]:
    """Retrieve the first family contact for a patient from the memory service."""
    from . import memory

    patient: Optional[PatientProfile] = memory.get_patient(patient_id)
    if patient and patient.family_contacts:
        return patient.family_contacts[0]
    return None


def _extract_requested_contact_name(transcript: str) -> str:
    """Pull an explicit recipient name from contact/call requests."""
    text = transcript.strip()
    lower = text.lower()

    def clean(candidate: str) -> str:
        candidate = re.split(r"\b(?:saying|that|to say|and say|please|for me)\b", candidate, flags=re.IGNORECASE)[0]
        candidate = candidate.strip(" .!?")
        candidate = re.sub(r"^(my|the)\s+", "", candidate, flags=re.IGNORECASE)
        words = candidate.split()
        if len(words) > 1 and words[1].lower().strip(" .!?") in _MESSAGE_BODY_STARTERS:
            candidate = words[0]
        if candidate.lower() in {"daughter", "son", "granddaughter", "grandson", "family", "family member"}:
            return ""
        return candidate.title() if candidate else ""

    for marker in (
        "send a message to ", "send message to ", "send a text to ", "send text to ",
        "make a message to ", "write a message to ", "draft a message to ",
        "compose a message to ", "create a message to ",
    ):
        idx = lower.find(marker)
        if idx != -1:
            return clean(text[idx + len(marker):])

    greeting_match = re.search(r"\b(?:say|send)\s+.+?\s+to\s+(.+)$", text, flags=re.IGNORECASE)
    if greeting_match:
        return clean(greeting_match.group(1))

    match = re.search(r"\bsend\s+(.+?)\s+(?:a\s+)?(?:text|message)\b", text, flags=re.IGNORECASE)
    if match:
        return clean(match.group(1))

    for marker in ("text ", "message "):
        idx = lower.find(marker)
        if idx != -1:
            return clean(text[idx + len(marker):])

    for marker in ("video call ", "facetime ", "phone ", "call "):
        idx = lower.find(marker)
        if idx != -1:
            return clean(text[idx + len(marker):])

    for marker in ("tell ", "ask "):
        idx = lower.find(marker)
        if idx != -1:
            return clean(text[idx + len(marker):])

    lower = text.lower()
    relation_words = {"daughter", "son", "granddaughter", "grandson", "wife", "husband", "family"}
    if any(word in lower for word in relation_words):
        return ""

    return ""


def _extract_message_body(transcript: str, contact_name: str, patient_name: str) -> str:
    patterns = (
        r"\b(?:saying|that|to say|and say)\s+(.+)$",
        r"\btell\s+.+?\s+that\s+(.+)$",
        r"\b(?:say|send)\s+(.+?)\s+to\s+.+$",
    )
    for pattern in patterns:
        match = re.search(pattern, transcript, flags=re.IGNORECASE)
        if match:
            body = match.group(1).strip(" .")
            if body and body.lower() not in {"a message", "message", "a text", "text"}:
                return body

    if contact_name and contact_name.lower() != "family member":
        name_pattern = re.escape(contact_name)
        for pattern in (
            rf"\b(?:(?:send|make|write|draft|compose|create)\s+(?:a\s+)?(?:text|message)\s+to|text|message)\s+{name_pattern}\s+(.+)$",
            rf"\b(?:tell|ask|say to)\s+{name_pattern}\s+(.+)$",
        ):
            match = re.search(pattern, transcript, flags=re.IGNORECASE)
            if not match:
                continue
            body = match.group(1).strip(" .")
            first = body.split(maxsplit=1)[0].lower() if body else ""
            if first in _MESSAGE_BODY_STARTERS:
                return body

    return f"Hi {contact_name}, this is a message from {patient_name}."


def _fallback_message_intent(req: CareRequest) -> MessageIntent:
    recipient_name = _extract_requested_contact_name(req.transcript)
    if not recipient_name:
        contact = _get_family_contact(req.patient_id)
        recipient_name = contact.name if contact else "family member"
    body = _extract_message_body(req.transcript, recipient_name, _get_patient_name(req.patient_id))
    return MessageIntent(recipient_name=recipient_name, body=body)


async def _parse_message_intent(req: CareRequest) -> MessageIntent:
    fallback = _fallback_message_intent(req)
    if not settings.anthropic_api_key:
        return fallback

    from anthropic import AsyncAnthropic  # type: ignore

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    try:
        resp = await client.messages.create(
            model=settings.anthropic_model,
            max_tokens=200,
            system=MESSAGE_INTENT_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Patient request transcript: {req.transcript!r}\n"
                        f"Patient context:\n{req.patient_context or '(none)'}"
                    ),
                }
            ],
        )
        data = _extract_json(resp.content[0].text)  # type: ignore[union-attr]
    except Exception:
        logger.exception("Claude message parsing failed — falling back to deterministic parser")
        return fallback

    recipient_name = str(data.get("recipient_name") or "").strip()
    body = str(data.get("body") or "").strip(" .")
    if not recipient_name:
        recipient_name = fallback.recipient_name
    if not body:
        body = fallback.body
    return MessageIntent(recipient_name=recipient_name, body=body)


_contacts_launched = False


def _ensure_contacts_running() -> None:
    """Launch Contacts.app in the background so AppleScript queries don't hit
    error -600 ("Application isn't running"). Runs at most once per process."""
    global _contacts_launched
    if _contacts_launched or sys.platform != "darwin":
        return
    try:
        # -g: don't bring to foreground, -j: launch hidden, -a: by app name.
        subprocess.run(["open", "-gja", "Contacts"], timeout=5, check=False)
    except Exception as exc:
        logger.warning("Could not launch Contacts.app: %s", exc)
    _contacts_launched = True


def _note_contacts_permission_error(stderr: str) -> None:
    """Surface the macOS Automation permission issue with an actionable hint."""
    if "-1743" in stderr or "Not authorized" in stderr:
        logger.warning(
            "macOS denied Apple Events to Contacts (-1743). Grant access under "
            "System Settings > Privacy & Security > Automation, enabling Contacts "
            "for the app that runs this backend (Terminal / your IDE / Tendly)."
        )


def _lookup_macos_contact(name: str) -> Optional[FamilyContact]:
    """Look up a single named person in macOS Contacts without listing contacts."""
    if not name or sys.platform != "darwin":
        return None

    _ensure_contacts_running()

    script = r'''
tell application "Contacts"
    launch
    set searchName to __SEARCH_NAME__
    set matches to people whose name contains searchName
    if (count of matches) = 0 then return ""

    set p to missing value
    repeat with candidate in matches
        set candidatePhone to ""
        set candidateEmail to ""
        try
            if (count of phones of candidate) > 0 then set candidatePhone to value of phone 1 of candidate
        end try
        try
            if (count of emails of candidate) > 0 then set candidateEmail to value of email 1 of candidate
        end try
        if candidatePhone is not "" or candidateEmail is not "" then
            set p to candidate
            exit repeat
        end if
    end repeat

    if p is missing value then set p to item 1 of matches

    set personName to name of p
    set phoneValue to ""
    set emailValue to ""
    try
        if (count of phones of p) > 0 then set phoneValue to value of phone 1 of p
    end try
    try
        if (count of emails of p) > 0 then set emailValue to value of email 1 of p
    end try
    return personName & tab & phoneValue & tab & emailValue
end tell
'''.replace("__SEARCH_NAME__", json.dumps(name))

    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception as exc:
        logger.warning("macOS Contacts lookup failed for %s: %s", name, exc)
        return None

    if proc.returncode != 0:
        logger.warning("macOS Contacts lookup failed for %s: %s", name, proc.stderr.strip())
        _note_contacts_permission_error(proc.stderr)
        return None

    parts = proc.stdout.strip().split("\t")
    if not parts or not parts[0]:
        return None

    return FamilyContact(
        name=parts[0],
        relation="contact",
        phone=parts[1] if len(parts) > 1 and parts[1] else None,
        email=parts[2] if len(parts) > 2 and parts[2] else None,
    )


def _list_macos_contact_names() -> list[str]:
    """Return macOS contact names for local fuzzy matching, without reading values."""
    if sys.platform != "darwin":
        return []

    _ensure_contacts_running()

    script = r'''
tell application "Contacts"
    launch
    return name of people
end tell
'''

    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except Exception as exc:
        logger.warning("macOS Contacts name listing failed: %s", exc)
        return []

    if proc.returncode != 0:
        logger.warning("macOS Contacts name listing failed: %s", proc.stderr.strip())
        _note_contacts_permission_error(proc.stderr)
        return []

    names = [name.strip() for name in proc.stdout.strip().split(", ") if name.strip()]
    return sorted(set(names))


def _score_contact_name(spoken_name: str, candidate_name: str) -> float:
    spoken = re.sub(r"[^a-z0-9]+", " ", spoken_name.lower()).strip()
    candidate = re.sub(r"[^a-z0-9]+", " ", candidate_name.lower()).strip()
    if not spoken or not candidate:
        return 0.0

    def loose_key(value: str) -> str:
        value = re.sub(r"[^a-z0-9]+", "", value.lower())
        if len(value) <= 2:
            return value
        return value[0] + re.sub(r"[aeiouy]", "", value[1:])

    candidate_parts = candidate.split()
    relation_words = {"aunt", "auntie", "uncle", "mom", "mother", "dad", "father", "grandma", "grandpa"}
    first_name = candidate_parts[0]
    first_score = SequenceMatcher(None, spoken, first_name).ratio()
    if loose_key(spoken) and loose_key(spoken) == loose_key(first_name):
        first_score = max(first_score, 0.95)

    other_parts = candidate_parts[1:]
    other_score = max((SequenceMatcher(None, spoken, part).ratio() for part in other_parts), default=0.0)
    if first_name in relation_words:
        other_score *= 0.78
    else:
        other_score *= 0.88

    whole_score = SequenceMatcher(None, spoken, candidate).ratio()
    return max(first_score, other_score, whole_score)


def _local_best_contact_name(spoken_name: str, candidates: list[str]) -> tuple[str, float]:
    scored = sorted(
        ((candidate, _score_contact_name(spoken_name, candidate)) for candidate in candidates),
        key=lambda item: item[1],
        reverse=True,
    )
    return scored[0] if scored else ("", 0.0)


def _llm_best_contact_name(spoken_name: str, candidates: list[str]) -> tuple[str, float]:
    """Ask Claude to map a possibly misheard name to one local contact name."""
    if not settings.allow_contact_llm_matching or not settings.anthropic_api_key or not candidates:
        return "", 0.0

    from anthropic import Anthropic  # type: ignore

    candidate_names = candidates[:500]
    prompt = (
        "The user spoke a contact name that may be phonetically misspelled by speech-to-text.\n"
        "Choose the single closest contact name from the list, or return an empty selected_name if none are close.\n"
        "Return ONLY JSON like {\"selected_name\":\"Edan\",\"confidence\":0.92}.\n\n"
        f"Spoken name: {spoken_name!r}\n"
        "Contact names:\n"
        + "\n".join(f"- {name}" for name in candidate_names)
    )

    try:
        client = Anthropic(api_key=settings.anthropic_api_key)
        resp = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=120,
            system="You match short spoken contact names to a provided contact list. Never invent names.",
            messages=[{"role": "user", "content": prompt}],
        )
        data = _extract_json(resp.content[0].text)  # type: ignore[union-attr]
    except Exception as exc:
        logger.warning("LLM contact matching failed for %s: %s", spoken_name, exc)
        return "", 0.0

    selected_name = str(data.get("selected_name", "")).strip()
    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0

    if selected_name not in candidates:
        return "", 0.0
    return selected_name, confidence


def _fuzzy_lookup_macos_contact(name: str) -> Optional[FamilyContact]:
    """Resolve STT-ish names like 'Edon' to nearby Contacts names, then fetch details."""
    candidates = _list_macos_contact_names()
    if not candidates:
        return None

    local_name, local_score = _local_best_contact_name(name, candidates)
    llm_name, llm_score = _llm_best_contact_name(name, candidates)

    chosen_name = ""
    if llm_name and llm_score >= 0.70:
        chosen_name = llm_name
    elif local_name and local_score >= 0.72:
        chosen_name = local_name

    if not chosen_name:
        logger.info("No fuzzy contact match for %s; best local score was %.2f", name, local_score)
        return None

    logger.info("Resolved spoken contact %r to %r", name, chosen_name)
    return _lookup_macos_contact(chosen_name)


def _resolve_contact(req: CareRequest) -> Optional[FamilyContact]:
    requested_name = _extract_requested_contact_name(req.transcript)
    family_contact = _get_family_contact(req.patient_id)

    if requested_name:
        if family_contact and requested_name.lower() in family_contact.name.lower():
            return family_contact
        mac_contact = _lookup_macos_contact(requested_name)
        if mac_contact:
            return mac_contact
        fuzzy_contact = _fuzzy_lookup_macos_contact(requested_name)
        if fuzzy_contact:
            return fuzzy_contact
        return FamilyContact(name=requested_name, relation="contact")

    return family_contact


def _resolve_contact_name(patient_id: str, name: str) -> FamilyContact:
    family_contact = _get_family_contact(patient_id)
    if family_contact and name and name.lower() in family_contact.name.lower():
        return family_contact

    mac_contact = _lookup_macos_contact(name)
    if mac_contact:
        return mac_contact
    fuzzy_contact = _fuzzy_lookup_macos_contact(name)
    if fuzzy_contact:
        return fuzzy_contact

    return FamilyContact(name=name or "family member", relation="contact")


def _get_patient_name(patient_id: str) -> str:
    """Retrieve patient name from memory."""
    from . import memory

    patient = memory.get_patient(patient_id)
    return patient.name if patient else "the patient"


def plan_task(req: CareRequest) -> str:
    """Human-readable description of the automated action to be taken.

    Used as the patient confirmation prompt (§3.4.3): "I'm going to [plan].
    Should I go ahead?"
    """
    task_type = _classify_task(req)
    contact = _resolve_contact(req)
    contact_desc = f"{contact.name} ({contact.relation})" if contact else "family member"

    video_call = any(kw in req.transcript.lower() for kw in ("video", "facetime", "zoom"))

    plans = {
        _TASK_VIDEO_CALL: (
            f"Start a video call with {contact_desc}."
            if video_call else f"Start a phone call with {contact_desc}."
        ),
        _TASK_SEND_MESSAGE: f"Send a message to {contact_desc}.",
        _TASK_OPEN_EMAIL: "Open your email inbox.",
        _TASK_OPEN_PORTAL: "Open the patient health portal.",
        _TASK_PLAY_MEDIA: "Open a music or media player and start playing something you'll enjoy.",
        _TASK_OPEN_WEBSITE: "Open the web browser and navigate to the requested page.",
        _TASK_GENERAL_NAV: "Perform the requested digital task on your computer.",
    }
    return plans.get(task_type, "Perform the requested digital task.")


def _extract_json(text: str) -> dict:
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = text.replace("```", "")
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"No JSON object found in model output: {text!r}")
    return json.loads(text[start : end + 1])


def _google_search_url(query: str) -> str:
    return f"https://www.google.com/search?q={quote_plus(query.strip())}"


def _google_site_search_url(site: str, query: str) -> str:
    domain = urlparse(_KNOWN_SITE_URLS.get(site, site)).netloc or site
    return _google_search_url(f"site:{domain} {query}")


def _site_search_url(site: str, query: str) -> str:
    site_key = re.sub(r"[^a-z0-9]+", "", site.lower())
    encoded = quote_plus(query.strip())
    if not encoded:
        return _KNOWN_SITE_URLS.get(site_key, _google_search_url(site))

    site_search_urls = {
        "amazon": f"https://www.amazon.com/s?k={encoded}",
        "google": _google_search_url(query),
        "gmail": f"https://mail.google.com/mail/u/0/#search/{encoded}",
        "instagram": f"https://www.instagram.com/explore/search/keyword/?q={encoded}",
        "linkedin": f"https://www.linkedin.com/search/results/all/?keywords={encoded}",
        "mail": f"https://mail.google.com/mail/u/0/#search/{encoded}",
        "nytimes": f"https://www.nytimes.com/search?query={encoded}",
        "wikipedia": f"https://www.wikipedia.org/search-redirect.php?search={encoded}",
        "youtube": f"https://www.youtube.com/results?search_query={encoded}",
    }
    if site_key in site_search_urls:
        return site_search_urls[site_key]
    return _google_site_search_url(site_key, query)


def _normalize_target_url(target_url: str, search_query: str = "") -> str:
    target_url = (target_url or "").strip()
    if not target_url and search_query:
        return _google_search_url(search_query)
    if not target_url:
        return "https://www.google.com"
    if target_url.startswith("mailto:"):
        return target_url
    if " " in target_url:
        return _google_search_url(search_query or target_url)
    if "://" not in target_url:
        target_url = f"https://{target_url}"
    parsed = urlparse(target_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return _google_search_url(search_query or target_url)
    return target_url


def _known_site_from_text(text: str) -> str:
    compact_text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    for site in sorted(_KNOWN_SITE_URLS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(site)}\b", compact_text):
            return site
    return ""


def _clean_open_target(transcript: str) -> str:
    text = transcript.strip()
    lowered = text.lower()
    for prefix in (
        "open up ", "open ", "go to ", "navigate to ", "browse to ",
        "pull up ", "bring up ", "can you open ", "please open ",
    ):
        if lowered.startswith(prefix):
            return text[len(prefix):].strip(" .!?")
    return text.strip(" .!?")


def _extract_site_search(transcript: str) -> tuple[str, str]:
    site = _known_site_from_text(transcript)
    if not site:
        return "", ""

    patterns = (
        rf"\b(?:go to|open|open up|pull up|bring up)\s+{re.escape(site)}\s+(?:and\s+)?(?:search(?: for)?|look up|find)\s+(.+)$",
        rf"\b(?:search(?: for)?|look up|find)\s+(.+?)\s+(?:on|in)\s+{re.escape(site)}\b",
        rf"\b{re.escape(site)}\s+(?:search(?: for)?|look up|find)\s+(.+)$",
    )
    for pattern in patterns:
        match = re.search(pattern, transcript, flags=re.IGNORECASE)
        if match:
            query = match.group(1).strip(" .!?")
            if query:
                return site, query

    return "", ""


def _fallback_automation_plan(req: CareRequest) -> AutomationPlan:
    transcript = req.transcript.strip()
    lowered = transcript.lower()

    site, site_query = _extract_site_search(transcript)
    if site and site_query:
        return AutomationPlan(
            action="site_search",
            target_url=_site_search_url(site, site_query),
            search_query=site_query,
            site=site,
            description=f"Search {site.title()} for {site_query}.",
        )

    if any(lowered.startswith(prefix) for prefix in (
        "find ", "find me ", "search ", "search for ", "look up ",
        "look for ", "show me ", "google ",
    )):
        query = re.sub(
            r"^(find me|find|search for|search|look up|look for|show me|google)\s+",
            "",
            transcript,
            flags=re.IGNORECASE,
        ).strip(" .!?")
        return AutomationPlan(
            action="search_web",
            target_url=_google_search_url(query),
            search_query=query,
            site="",
            description=f"Search the web for {query}.",
        )

    target = _clean_open_target(transcript)
    target_key = re.sub(r"[^a-z0-9]+", "", target.lower())
    if target_key in _KNOWN_SITE_URLS:
        return AutomationPlan(
            action="open_url",
            target_url=_KNOWN_SITE_URLS[target_key],
            site=target_key,
            description=f"Open {target}.",
        )
    if "." in target and " " not in target:
        return AutomationPlan(
            action="open_url",
            target_url=_normalize_target_url(target),
            site="",
            description=f"Open {target}.",
        )

    query = target or transcript
    return AutomationPlan(
        action="search_web",
        target_url=_google_search_url(query),
        search_query=query,
        site="",
        description=f"Search the web for {query}.",
    )


async def _plan_automation(req: CareRequest) -> AutomationPlan:
    site_first_plan = _fallback_automation_plan(req)
    if site_first_plan.action == "site_search":
        return site_first_plan

    if not settings.anthropic_api_key:
        return site_first_plan

    from anthropic import AsyncAnthropic  # type: ignore

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    try:
        resp = await client.messages.create(
            model=settings.anthropic_model,
            max_tokens=300,
            system=AUTOMATION_PLANNER_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Patient request: {req.transcript!r}\n"
                        f"Patient context:\n{req.patient_context or '(none)'}"
                    ),
                }
            ],
        )
        text: str = resp.content[0].text  # type: ignore[union-attr]
        data = _extract_json(text)
        action = data.get("action") if data.get("action") in {"open_url", "search_web", "site_search"} else "search_web"
        search_query = str(data.get("search_query") or "").strip()
        site = str(data.get("site") or "").strip().lower()
        target_url = str(data.get("target_url") or "")
        if action == "site_search" and search_query:
            target_url = _site_search_url(site or _known_site_from_text(req.transcript), search_query)
        else:
            target_url = _normalize_target_url(target_url, search_query)
        description = str(data.get("description") or "").strip() or site_first_plan.description
        return AutomationPlan(
            action=action,
            target_url=target_url,
            search_query=search_query,
            site=site,
            description=description,
        )
    except Exception:
        logger.exception("Claude automation planning failed — falling back to deterministic planner")
        return site_first_plan


# ---------------------------------------------------------------------------
# Simulang script generation (real — produces valid simulang-js TypeScript)
# ---------------------------------------------------------------------------


def _generate_script(req: CareRequest) -> str:
    """Generate a valid simulang TypeScript script for the given task.

    These scripts use the @simular-ai/simulang-js API:
    - App.defaultBrowser().open(url, FocusPolicy, Visibility, waitForLoad)
    - AccessibilityTree.fromPid(...).snapshot(true) for visible UI state
    - KeyboardController instances for hardware keyboard fallback
    See: https://docs.simular.ai/simulang/simulang-primer
    """
    task_type = _classify_task(req)
    contact = _resolve_contact(req)

    if task_type == _TASK_OPEN_EMAIL:
        return _script_open_url(
            url="https://mail.google.com",
            description="Opening email client",
        )

    elif task_type == _TASK_OPEN_PORTAL:
        return _script_open_url(
            url="https://mychart.org",
            description="Opening patient health portal",
        )

    elif task_type == _TASK_PLAY_MEDIA:
        # Try to extract what they want to play from transcript
        query = _extract_media_query(req.transcript)
        url = f"https://www.youtube.com/results?search_query={query}" if query else "https://open.spotify.com"
        return _script_open_url(
            url=url,
            description=f"Playing media: {query}" if query else "Opening music player",
        )

    elif task_type == _TASK_VIDEO_CALL:
        contact_name = contact.name if contact else "family member"
        contact_phone = contact.phone if contact else None
        video = any(kw in req.transcript.lower() for kw in ("video", "facetime", "zoom"))
        return _script_call(contact_name, contact_phone, video=video)

    elif task_type == _TASK_SEND_MESSAGE:
        contact_name = contact.name if contact else "family member"
        contact_phone = contact.phone if contact else None
        contact_email = contact.email if contact else None
        # Prefer phone for SMS; Contacts lookup happens in run_task and is passed via req.
        contact_phone = getattr(req, "_resolved_phone", None) or (contact.phone if contact else None)
        message = _extract_message_body(req.transcript, contact_name, _get_patient_name(req.patient_id))
        return _script_send_message(contact_name, contact_phone, contact_email, message)

    elif task_type == _TASK_OPEN_WEBSITE:
        url = _extract_url_from_transcript(req.transcript)
        return _script_open_url(
            url=url,
            description="Opening requested website",
        )

    else:
        # General navigation — open browser to a search
        query = req.transcript.replace('"', '\\"')
        url = f"https://www.google.com/search?q={query}"
        return _script_open_url(
            url=url,
            description="General web navigation",
        )


def _generate_script_from_plan(plan: AutomationPlan) -> str:
    if plan.action == "site_search":
        return _script_site_search(plan)
    return _script_open_url(
        url=plan.target_url,
        description=plan.description,
    )


def _simulang_imports() -> str:
    return (
        "import {\n"
        "  AccessibilityTree,\n"
        "  App,\n"
        "  AriaRole,\n"
        "  Direction,\n"
        "  FocusPolicy,\n"
        "  Key,\n"
        "  KeyboardController,\n"
        "  Visibility,\n"
        "  type AccessibilityNodeJs,\n"
        "} from '@simular-ai/simulang-js'"
    )


def _simulang_helpers() -> str:
    return """\
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

function flattenDFS(node: AccessibilityNodeJs, out: AccessibilityNodeJs[] = []): AccessibilityNodeJs[] {
  out.push(node)
  for (const child of node.children) flattenDFS(child, out)
  return out
}

const stripPUA = (value: string) =>
  value.replace(/[\\uE000-\\uF8FF]/g, '').replace(/\\s+/g, ' ').trim()

const labelOf = (node: AccessibilityNodeJs) =>
  stripPUA([node.name, node.value, node.description, node.helpText].filter(Boolean).join(' '))

function pageNodes(root: AccessibilityNodeJs): AccessibilityNodeJs[] {
  const all = flattenDFS(root)
  const docs = all.filter((node) => node.role === AriaRole.Document)
  return docs.length ? flattenDFS(docs[docs.length - 1]) : all
}

function findNode(nodes: AccessibilityNodeJs[], roles: AriaRole[], label: RegExp) {
  return nodes.find((node) => roles.includes(node.role) && node.refId != null && label.test(labelOf(node)))
}

async function withSnapshot<T>(
  tree: AccessibilityTree,
  predicate: (nodes: AccessibilityNodeJs[]) => T | null | undefined,
  label: string,
  timeoutMs = 8000,
  intervalMs = 250,
): Promise<T> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const result = predicate(pageNodes(tree.snapshot(false)))
    if (result) return result
    await sleep(intervalMs)
  }
  throw new Error(`Timed out waiting for ${label}`)
}

async function waitForBrowserDocument(instance: any, label: string) {
  await sleep(1500)
  if (!instance.isAccessible()) instance.enableAccessibility()
  const tree = AccessibilityTree.fromPid(instance.pid)
  await withSnapshot(
    tree,
    (nodes) => nodes.some((node) => node.role === AriaRole.Document) ? true : null,
    `browser document for ${label}`,
  )
}

function fail(error: unknown): never {
  console.error('[Tendly] Simulang failed:', error instanceof Error ? error.message : error)
  process.exit(1)
}

function keyClick(keyboard: KeyboardController, key: Key) {
  keyboard.key(key, Direction.Click)
}

async function chord(keyboard: KeyboardController, keys: Key[]) {
  for (const key of keys) keyboard.key(key, Direction.Press)
  await sleep(80)
  for (const key of [...keys].reverse()) keyboard.key(key, Direction.Release)
}
"""


def _script_site_search(plan: AutomationPlan) -> str:
    """Simulang script: open a site-specific search result page."""
    safe_url = json.dumps(plan.target_url)
    safe_description = json.dumps(plan.description)
    safe_site = json.dumps(plan.site or "site")
    safe_query = json.dumps(plan.search_query)
    return f"""\
// Simulang script: {plan.description}
// Generated by Tendly automation service
{_simulang_imports()}
{_simulang_helpers()}

try {{
  console.log('[Tendly] ' + {safe_description})
  console.log('[Tendly] Searching ' + {safe_site} + ' for ' + {safe_query})
  const instance = App.defaultBrowser().open({safe_url}, FocusPolicy.Steal, Visibility.Show, true)
  await waitForBrowserDocument(instance, {safe_site})
  console.log('[Tendly] Done: Simulang opened site search at ' + {safe_url})
}} catch (error) {{
  fail(error)
}}
"""


def _script_open_url(url: str, description: str) -> str:
    """Simulang script: open a URL in the default browser."""
    safe_url = json.dumps(url)
    safe_description = json.dumps(description)
    return f"""\
// Simulang script: {description}
// Generated by Tendly automation service
{_simulang_imports()}
{_simulang_helpers()}

try {{
  console.log('[Tendly] ' + {safe_description})
  const instance = App.defaultBrowser().open({safe_url}, FocusPolicy.Steal, Visibility.Show, true)
  await waitForBrowserDocument(instance, {safe_description})
  console.log('[Tendly] Done: Simulang opened browser to ' + {safe_url})
}} catch (error) {{
  fail(error)
}}
"""


def _phone_uri_value(phone: Optional[str]) -> str:
    if not phone:
        return ""
    return re.sub(r"[^0-9+]", "", phone)


def _script_call(contact_name: str, contact_phone: Optional[str], *, video: bool) -> str:
    """Simulang script: start a phone or FaceTime call via the native app."""
    phone = _phone_uri_value(contact_phone)
    recipient = phone or contact_name
    call_kind = "FaceTime video call" if video else "FaceTime audio call"
    safe_recipient = json.dumps(recipient)
    safe_contact_name = json.dumps(contact_name)
    safe_kind = json.dumps(call_kind)
    call_label_re = "video|facetime|call" if video else "audio|phone|call"
    return f"""\
// Simulang script: Start {call_kind} with {contact_name}
// Generated by Tendly automation service
{_simulang_imports()}
{_simulang_helpers()}

try {{
  console.log('[Tendly] Starting ' + {safe_kind} + ' with ' + {safe_contact_name})
  const instance = App.exactName('FaceTime').open(null, FocusPolicy.Steal, Visibility.Show, true)
  await sleep(1200)
  if (!instance.isAccessible()) instance.enableAccessibility()

  const tree = AccessibilityTree.fromPid(instance.pid)
  const nodes = pageNodes(tree.snapshot(false))
  const searchBox = findNode(nodes, [AriaRole.Textbox], /name|email|phone|search|to/i)
    ?? nodes.find((n) => n.role === AriaRole.Textbox && n.refId != null)

  if (searchBox?.refId != null) {{
    tree.setValue(searchBox.refId, {safe_recipient})
  }} else {{
    const keyboard = new KeyboardController()
    keyboard.text({safe_recipient})
  }}

  await sleep(500)
  const freshNodes = pageNodes(tree.snapshot(false))
  const callButton = findNode(freshNodes, [AriaRole.Button], /{call_label_re}/i)
  if (callButton?.refId == null) throw new Error('Could not find a FaceTime call button.')
  tree.activate(callButton.refId)
  console.log('[Tendly] Done: FaceTime handoff started for ' + {safe_contact_name})
}} catch (error) {{
  fail(error)
}}
"""


def _script_send_message(
    contact_name: str,
    contact_phone: Optional[str],
    contact_email: Optional[str],
    message: str,
) -> str:
    """Simulang script: draft a confirmed message to a family contact."""
    recipient = _phone_uri_value(contact_phone) or contact_email or contact_name
    safe_recipient = json.dumps(recipient)
    safe_message = json.dumps(message)
    safe_contact_name = json.dumps(contact_name)
    safe_search_name = json.dumps(contact_name)
    return f"""\
// Simulang script: Draft message to {contact_name}
// Generated by Tendly automation service
{_simulang_imports()}
{_simulang_helpers()}

try {{
  console.log('[Tendly] Opening Messages compose for ' + {safe_contact_name})
  const instance = App.exactName('Messages').open(null, FocusPolicy.Steal, Visibility.Show, true)
  await sleep(1200)
  if (!instance.isAccessible()) instance.enableAccessibility()

  const tree = AccessibilityTree.fromPid(instance.pid)
  const keyboard = new KeyboardController()

  const findMessageBodyField = async (timeoutMs = 3500) => withSnapshot(
    tree,
    (nodes) => {{
      const fields = nodes.filter((node) => {{
        if (node.role !== AriaRole.Textbox || node.refId == null) return false
        return !/search|to|recipient|name|phone|email/i.test(labelOf(node))
      }})
      return fields.find((node) => /message|imessage|text message/i.test(labelOf(node))) ?? fields[fields.length - 1]
    }},
    'Messages body field',
    timeoutMs,
  )

  const searchField = await withSnapshot(
    tree,
    (nodes) => findNode(nodes, [AriaRole.Textbox, AriaRole.Searchbox], /search/i),
    'Messages search field',
    2500,
  ).catch(() => null)

  if (searchField?.refId != null) {{
    tree.setValue(searchField.refId, {safe_search_name})
    await sleep(900)
    keyClick(keyboard, Key.DownArrow)
    await sleep(150)
    keyClick(keyboard, Key.Return)
    await sleep(900)
    keyClick(keyboard, Key.Escape)
    await sleep(500)

    const existingBodyField = await findMessageBodyField(2500).catch(() => null)
    if (existingBodyField?.refId != null) {{
      tree.setValue(existingBodyField.refId, {safe_message})
      console.log('[Tendly] Done: Simulang drafted a message in an existing Messages conversation for ' + {safe_contact_name})
      process.exit(0)
    }}
  }}

  const composeButton = await withSnapshot(
    tree,
    (nodes) => findNode(nodes, [AriaRole.Button], /new message|compose|new/i),
    'Messages compose button',
    2500,
  ).catch(() => null)

  if (composeButton?.refId != null) {{
    tree.activate(composeButton.refId)
  }} else {{
    await chord(keyboard, [Key.Meta, Key.N])
  }}
  await sleep(800)

  const recipientField = await withSnapshot(
    tree,
    (nodes) =>
      findNode(nodes, [AriaRole.Textbox, AriaRole.Combobox], /to|recipient|name|phone|email/i)
      ?? nodes.find((node) =>
        (node.role === AriaRole.Textbox || node.role === AriaRole.Combobox) && node.refId != null
      ),
    'Messages recipient field',
  )

  if (recipientField.refId == null) throw new Error('Messages recipient field did not expose a refId.')
  tree.setValue(recipientField.refId, {safe_recipient})
  await sleep(1200)
  keyClick(keyboard, Key.DownArrow)
  await sleep(250)
  keyClick(keyboard, Key.Return)
  await sleep(700)
  keyClick(keyboard, Key.Tab)
  await sleep(400)

  const messageField = await withSnapshot(
    tree,
    (nodes) => {{
      const fields = nodes.filter((node) => {{
        if (node.role !== AriaRole.Textbox || node.refId == null) return false
        return !/search|to|recipient|name|phone|email/i.test(labelOf(node))
      }})
      return fields.find((node) => /message|imessage|text message/i.test(labelOf(node))) ?? fields[fields.length - 1]
    }},
    'Messages body field',
  )

  if (messageField.refId == null) throw new Error('Messages body field did not expose a refId.')
  tree.setValue(messageField.refId, {safe_message})
  console.log('[Tendly] Done: Simulang drafted a Messages conversation for ' + {safe_contact_name})
}} catch (error) {{
  fail(error)
}}
"""


def _extract_media_query(transcript: str) -> str:
    """Extract a media/music search query from the transcript."""
    t = transcript.lower()
    # Remove common prefixes
    for prefix in ("play ", "put on ", "i want to hear ", "can you play ",
                   "please play ", "play some ", "i'd like to listen to "):
        if t.startswith(prefix):
            return transcript[len(prefix):].strip()
    # If it just says "play music" or similar, return empty
    if t in ("play music", "play something", "music"):
        return ""
    return transcript.strip()


def _extract_url_from_transcript(transcript: str) -> str:
    """Try to extract a URL or website name from the transcript."""
    t = transcript.lower()
    # Remove common prefixes
    for prefix in ("open ", "go to ", "navigate to ", "browse to ",
                   "can you open ", "please open "):
        if t.startswith(prefix):
            remainder = transcript[len(prefix):].strip()
            # If it looks like a URL, use it directly
            if "." in remainder and " " not in remainder:
                if not remainder.startswith("http"):
                    return f"https://{remainder}"
                return remainder
            # Otherwise, Google it
            return _google_search_url(remainder)
    return "https://www.google.com"


# ---------------------------------------------------------------------------
# Simulang execution (real subprocess call, guarded by try-except)
# ---------------------------------------------------------------------------


async def _execute_simulang(script_content: str) -> dict:
    """Attempt to run a simulang script via the CLI.

    Returns {"status": "done", "detail": ...} on success,
    or raises RuntimeError if simulang is unavailable.
    """
    repo_root = Path(__file__).resolve().parents[3]
    frontend_dir = repo_root / "frontend"
    script_dir = frontend_dir / ".simulang-tasks"
    script_dir.mkdir(parents=True, exist_ok=True)

    # Keep generated scripts in one ignored project directory; `simulang run`
    # resolves @simular-ai/simulang-js via its bundled runtime by default.
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".mts",
        prefix="tendly_task_",
        dir=script_dir,
        delete=False,
    ) as f:
        f.write(script_content)
        script_path = Path(f.name)

    try:
        # Attempt to run via simulang CLI
        proc = await asyncio.create_subprocess_exec(
            "simulang", "run", str(script_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(frontend_dir),
            env={**os.environ, "NODE_NO_WARNINGS": "1"},
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)

        if proc.returncode == 0:
            output = stdout.decode().strip()
            logger.info("simulang task completed: %s", output)
            return {"status": "done", "detail": f"Task executed successfully. {output}"}
        else:
            error = stderr.decode().strip() or stdout.decode().strip()
            logger.warning("simulang failed (rc=%d): %s", proc.returncode, error)
            raise RuntimeError(f"simulang exited with code {proc.returncode}: {error}")

    except FileNotFoundError:
        raise RuntimeError(
            "simulang CLI not found on PATH. Install/authenticate Simulang, then rerun the task."
        )
    except asyncio.TimeoutError:
        raise RuntimeError("simulang execution timed out (30s)")
    finally:
        # Clean up temp script
        try:
            script_path.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# run_task — main entry point (real attempt + mock fallback)
# ---------------------------------------------------------------------------


async def run_task(req: CareRequest) -> dict:
    """Execute the automated task. Returns {status, detail}.

    Flow:
    1. Generate a simulang TypeScript script for the task.
    2. Attempt to execute it via `simulang run <script>`.
    3. On success: return {status: "done", detail: ...}.
    4. On failure (simulang unavailable / permissions / headless env):
       - If TENDLY_ALLOW_MOCKS is True: return a rich mock response.
       - Otherwise: raise the error.
    """
    task_type = _classify_task(req)

    plan = plan_task(req)
    automation_plan: Optional[AutomationPlan] = None
    contact = _resolve_contact(req)

    if task_type in {_TASK_OPEN_WEBSITE, _TASK_PLAY_MEDIA, _TASK_GENERAL_NAV}:
        automation_plan = await _plan_automation(req)
        plan = automation_plan.description
        script = _generate_script_from_plan(automation_plan)
    elif task_type == _TASK_SEND_MESSAGE:
        message_intent = await _parse_message_intent(req)
        contact = _resolve_contact_name(req.patient_id, message_intent.recipient_name)
        plan = f"Send a message to {contact.name} ({contact.relation})."
        script = _script_send_message(
            contact.name,
            contact.phone,
            contact.email,
            message_intent.body,
        )
    else:
        script = _generate_script(req)

    # Log the generated script for debugging
    logger.info(
        "Automation task: type=%s, patient=%s, plan=%s",
        task_type, req.patient_id, plan,
    )
    if automation_plan:
        logger.info(
            "Automation plan: action=%s site=%s target=%s query=%s",
            automation_plan.action,
            automation_plan.site,
            automation_plan.target_url,
            automation_plan.search_query,
        )
    logger.debug("Generated simulang script:\n%s", script)

    # Attempt real execution
    try:
        result = await _execute_simulang(script)
        return result
    except RuntimeError as exc:
        logger.warning("simulang execution failed: %s", exc)

        if not settings.allow_mocks:
            raise

        # Fall back to rich mock response
        mock_detail = _build_mock_detail(task_type, plan, contact, req)
        return {"status": "mocked", "detail": mock_detail}


def _build_mock_detail(
    task_type: str,
    plan: str,
    contact: Optional[FamilyContact],
    req: CareRequest,
) -> str:
    """Build a descriptive mock response that shows what simulang WOULD do."""
    contact_info = ""
    if contact:
        contact_info = f" (contact: {contact.name}, {contact.relation}"
        if contact.phone:
            contact_info += f", phone: {contact.phone}"
        if contact.email:
            contact_info += f", email: {contact.email}"
        contact_info += ")"

    details = {
        _TASK_VIDEO_CALL: f"[Simular mock] Would start video call{contact_info} "
                          f"via simulang App.defaultBrowser().open('https://meet.google.com/new')",
        _TASK_SEND_MESSAGE: f"[Simular mock] Would send message{contact_info} "
                            f"via simulang browser mailto: link",
        _TASK_OPEN_EMAIL: "[Simular mock] Would open email via simulang "
                          "App.defaultBrowser().open('https://mail.google.com')",
        _TASK_OPEN_PORTAL: "[Simular mock] Would open patient portal via simulang "
                           "App.defaultBrowser().open('https://mychart.org')",
        _TASK_PLAY_MEDIA: f"[Simular mock] Would open media player via simulang — plan: {plan}",
        _TASK_OPEN_WEBSITE: f"[Simular mock] Would navigate/search browser via simulang — plan: {plan}",
        _TASK_GENERAL_NAV: f"[Simular mock] Would perform web navigation via simulang — plan: {plan}",
    }
    return details.get(task_type, f"[Simular mock] Would: {plan}")
