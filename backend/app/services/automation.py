"""Computer automation via Simular `simulang` (§3.4).

CONTRACT: `run_task(req) -> dict` executes a digital task for the patient and
returns {status, detail}. `plan_task(req) -> str` returns a human-readable
description of the action to be taken (used for the patient confirmation prompt).

Integration strategy:
- Generates TypeScript scripts targeting `@simular-ai/simulang-js` (the real
  desktop-automation library: browser opening, video calls, messaging, etc.)
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
import logging
import os
import tempfile
from typing import Optional

from ..config import get_settings
from ..models import CareRequest, Category, FamilyContact, PatientProfile

logger = logging.getLogger(__name__)

settings = get_settings()

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


def _classify_task(req: CareRequest) -> str:
    """Determine the specific automation task type from category + transcript."""
    t = req.transcript.lower()

    # Family communication category always maps to call/message
    if req.category == Category.family_communication:
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
    if any(kw in t for kw in ("browse", "website", "internet", "search", "google", "open")):
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
    contact = _get_family_contact(req.patient_id)
    contact_desc = f"{contact.name} ({contact.relation})" if contact else "family member"

    plans = {
        _TASK_VIDEO_CALL: f"Start a video call with {contact_desc}.",
        _TASK_SEND_MESSAGE: f"Send a message to {contact_desc}.",
        _TASK_OPEN_EMAIL: "Open your email inbox.",
        _TASK_OPEN_PORTAL: "Open the patient health portal.",
        _TASK_PLAY_MEDIA: "Open a music or media player and start playing something you'll enjoy.",
        _TASK_OPEN_WEBSITE: "Open the web browser and navigate to the requested page.",
        _TASK_GENERAL_NAV: "Perform the requested digital task on your computer.",
    }
    return plans.get(task_type, "Perform the requested digital task.")


# ---------------------------------------------------------------------------
# Simulang script generation (real — produces valid simulang-js TypeScript)
# ---------------------------------------------------------------------------


def _generate_script(req: CareRequest) -> str:
    """Generate a valid simulang TypeScript script for the given task.

    These scripts use the @simular-ai/simulang-js API:
    - App.defaultBrowser().open(url, FocusPolicy, Visibility, waitForLoad)
    - App.open(bundleId/path) for native apps
    See: https://github.com/simular-ai/simulang / SKILL.md
    """
    task_type = _classify_task(req)
    contact = _get_family_contact(req.patient_id)

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
        return _script_video_call(contact_name, contact_phone)

    elif task_type == _TASK_SEND_MESSAGE:
        contact_name = contact.name if contact else "family member"
        contact_email = contact.email if contact else None
        message = f"Hi {contact_name}, this is a message from {_get_patient_name(req.patient_id)}."
        return _script_send_message(contact_name, contact_email, message)

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


def _script_open_url(url: str, description: str) -> str:
    """Simulang script: open a URL in the default browser."""
    return f"""\
// Simulang script: {description}
// Generated by Tendly automation service
import {{ App, FocusPolicy, Visibility }} from '@simular-ai/simulang-js'

console.log('[Tendly] {description}')
const browser = App.defaultBrowser()
browser.open('{url}', FocusPolicy.Steal, Visibility.Show, true)
console.log('[Tendly] Done — browser opened to {url}')
"""


def _script_video_call(contact_name: str, contact_phone: Optional[str]) -> str:
    """Simulang script: start a video call (via browser-based Zoom/Meet)."""
    # In a real deployment, this would open a video call app or URL.
    # For the hackathon, we open a Google Meet link as an example.
    return f"""\
// Simulang script: Start video call with {contact_name}
// Generated by Tendly automation service
import {{ App, FocusPolicy, Visibility }} from '@simular-ai/simulang-js'

console.log('[Tendly] Starting video call with {contact_name}')
// Open Google Meet (or a pre-configured video call link)
const browser = App.defaultBrowser()
browser.open('https://meet.google.com/new', FocusPolicy.Steal, Visibility.Show, true)
console.log('[Tendly] Video call initiated for {contact_name}' +
            ({f"' (phone: {contact_phone})'" if contact_phone else "''"}) )
"""


def _script_send_message(contact_name: str, contact_email: Optional[str], message: str) -> str:
    """Simulang script: send a message to a family contact via email."""
    email = contact_email or "family@example.com"
    safe_message = message.replace("'", "\\'")
    return f"""\
// Simulang script: Send message to {contact_name}
// Generated by Tendly automation service
import {{ App, FocusPolicy, Visibility }} from '@simular-ai/simulang-js'

console.log('[Tendly] Sending message to {contact_name} at {email}')
const browser = App.defaultBrowser()
const mailtoUrl = 'mailto:{email}?subject=Message from Tendly&body={safe_message}'
browser.open(mailtoUrl, FocusPolicy.Steal, Visibility.Show, true)
console.log('[Tendly] Message compose window opened for {contact_name}')
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
            return f"https://www.google.com/search?q={remainder}"
    return "https://www.google.com"


# ---------------------------------------------------------------------------
# Simulang execution (real subprocess call, guarded by try-except)
# ---------------------------------------------------------------------------


async def _execute_simulang(script_content: str) -> dict:
    """Attempt to run a simulang script via the CLI.

    Returns {"status": "done", "detail": ...} on success,
    or raises RuntimeError if simulang is unavailable.
    """
    # Write script to a temp file
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ts", prefix="tendly_task_", delete=False
    ) as f:
        f.write(script_content)
        script_path = f.name

    try:
        # Attempt to run via simulang CLI
        proc = await asyncio.create_subprocess_exec(
            "simulang", "run", script_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "NODE_NO_WARNINGS": "1"},
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)

        if proc.returncode == 0:
            output = stdout.decode().strip()
            logger.info("simulang task completed: %s", output)
            return {"status": "done", "detail": f"Task executed successfully. {output}"}
        else:
            error = stderr.decode().strip()
            logger.warning("simulang failed (rc=%d): %s", proc.returncode, error)
            raise RuntimeError(f"simulang exited with code {proc.returncode}: {error}")

    except FileNotFoundError:
        raise RuntimeError("simulang CLI not found on PATH")
    except asyncio.TimeoutError:
        raise RuntimeError("simulang execution timed out (30s)")
    finally:
        # Clean up temp script
        try:
            os.unlink(script_path)
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
    script = _generate_script(req)

    # Log the generated script for debugging
    logger.info(
        "Automation task: type=%s, patient=%s, plan=%s",
        task_type, req.patient_id, plan,
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
        contact = _get_family_contact(req.patient_id)
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
        _TASK_OPEN_WEBSITE: f"[Simular mock] Would navigate browser via simulang — plan: {plan}",
        _TASK_GENERAL_NAV: f"[Simular mock] Would perform web navigation via simulang — plan: {plan}",
    }
    return details.get(task_type, f"[Simular mock] Would: {plan}")
