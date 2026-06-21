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
import json
import logging
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote_plus, urlparse

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


@dataclass
class AutomationPlan:
    action: str
    target_url: str
    description: str
    search_query: str = ""
    site: str = ""


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
        # Prefer phone for SMS; Contacts lookup happens in run_task and is passed via req
        contact_phone = getattr(req, "_resolved_phone", None) or (contact.phone if contact else None)
        message = f"Hi {contact_name}, this is a message from {_get_patient_name(req.patient_id)}."
        return _script_send_message(contact_name, contact_phone, message)

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


def _script_site_search(plan: AutomationPlan) -> str:
    """Simulang script: open a site-specific search result page."""
    safe_url = json.dumps(plan.target_url)
    safe_description = json.dumps(plan.description)
    safe_site = json.dumps(plan.site or "site")
    safe_query = json.dumps(plan.search_query)
    return f"""\
// Simulang script: {plan.description}
// Generated by Tendly automation service
import {{ App, FocusPolicy, Visibility }} from '@simular-ai/simulang-js'

console.log('[Tendly] ' + {safe_description})
console.log('[Tendly] Searching ' + {safe_site} + ' for ' + {safe_query})
const browser = App.defaultBrowser()
browser.open({safe_url}, FocusPolicy.Steal, Visibility.Show, true)
console.log('[Tendly] Done — site search opened to ' + {safe_url})
"""


def _script_open_url(url: str, description: str) -> str:
    """Simulang script: open a URL in the default browser."""
    safe_url = json.dumps(url)
    safe_description = json.dumps(description)
    return f"""\
// Simulang script: {description}
// Generated by Tendly automation service
import {{ App, FocusPolicy, Visibility }} from '@simular-ai/simulang-js'

console.log('[Tendly] ' + {safe_description})
const browser = App.defaultBrowser()
browser.open({safe_url}, FocusPolicy.Steal, Visibility.Show, true)
console.log('[Tendly] Done — browser opened to ' + {safe_url})
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


async def _lookup_contact_phone(name: str) -> Optional[str]:
    """Search macOS Contacts for a phone number by name via osascript."""
    if sys.platform != "darwin":
        return None
    # Sanitize name to avoid injection in AppleScript string
    safe_name = name.replace('"', "").replace("\\", "")
    script = (
        f'tell application "Contacts"\n'
        f'  set matched to people whose name contains "{safe_name}"\n'
        f'  if (count of matched) > 0 then\n'
        f'    set p to item 1 of matched\n'
        f'    if (count of phones of p) > 0 then\n'
        f'      return value of item 1 of phones of p\n'
        f'    end if\n'
        f'  end if\n'
        f'  return ""\n'
        f'end tell'
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        phone = stdout.decode().strip()
        return phone if phone else None
    except Exception:
        logger.debug("macOS Contacts lookup failed for %r", name)
        return None


def _normalise_phone(phone: str) -> str:
    """Strip formatting so we get a bare dialable number for sms: URLs."""
    return re.sub(r"[^\d+]", "", phone)


def _script_send_message(contact_name: str, phone: Optional[str], message: str) -> str:
    """Simulang script: open a Messages conversation with the contact via sms: URL."""
    target = f"sms:{_normalise_phone(phone)}" if phone else "https://messages.google.com"
    safe_contact = json.dumps(contact_name)
    safe_target  = json.dumps(target)
    safe_msg     = json.dumps(message)
    return f"""\
// Simulang script: Send text message to {contact_name}
// Generated by Tendly automation service
import {{ App, FocusPolicy, Visibility }} from '@simular-ai/simulang-js'

console.log('[Tendly] Opening Messages for ' + {safe_contact})
console.log('[Tendly] Message: ' + {safe_msg})
const browser = App.defaultBrowser()
browser.open({safe_target}, FocusPolicy.Steal, Visibility.Show, true)
console.log('[Tendly] Messages opened to {contact_name}')
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
        return await _execute_local_open(script_content)
    except asyncio.TimeoutError:
        raise RuntimeError("simulang execution timed out (30s)")
    finally:
        # Clean up temp script
        try:
            os.unlink(script_path)
        except OSError:
            pass


def _extract_open_target(script_content: str) -> Optional[str]:
    """Extract the URL/mailto target from the generated Simulang script."""
    consts = dict(re.findall(r"const\s+(\w+)\s*=\s*'([^']+)'", script_content))

    direct = re.search(r"browser\.open\((['\"])(.*?)\1", script_content)
    if direct:
        return direct.group(2)

    variable = re.search(r"browser\.open\((\w+),", script_content)
    if variable:
        return consts.get(variable.group(1))

    return None


async def _execute_local_open(script_content: str) -> dict:
    """Local fallback for generated browser-opening Simulang scripts.

    The hackathon app runs even when the `simulang` CLI is not installed. For
    scripts that only open a URL/mailto target, we can perform the same visible
    action locally instead of downgrading to a pure mock.
    """
    target = _extract_open_target(script_content)
    if not target:
        raise RuntimeError("simulang CLI not found on PATH")

    if sys.platform == "darwin":
        cmd = ["open", target]
    elif sys.platform.startswith("linux"):
        cmd = ["xdg-open", target]
    else:
        raise RuntimeError("simulang CLI not found on PATH")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)
    if proc.returncode != 0:
        error = stderr.decode().strip() or stdout.decode().strip()
        raise RuntimeError(f"local open fallback failed: {error}")

    logger.info("simulang CLI missing; opened target locally: %s", target)
    return {
        "status": "done",
        "detail": f"Opened {target} locally because the simulang CLI is not installed.",
    }


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

    # For message tasks, look up the contact's phone in macOS Contacts first
    if task_type == _TASK_SEND_MESSAGE:
        contact = _get_family_contact(req.patient_id)
        if contact:
            resolved = await _lookup_contact_phone(contact.name)
            if not resolved and contact.phone:
                resolved = contact.phone  # fall back to stored phone
            req._resolved_phone = resolved  # type: ignore[attr-defined]
            logger.info("Resolved phone for %s: %s", contact.name, resolved or "(none found)")

    plan = plan_task(req)
    automation_plan: Optional[AutomationPlan] = None
    if task_type in {_TASK_OPEN_WEBSITE, _TASK_PLAY_MEDIA, _TASK_GENERAL_NAV}:
        automation_plan = await _plan_automation(req)
        plan = automation_plan.description
        script = _generate_script_from_plan(automation_plan)
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
        _TASK_SEND_MESSAGE: f"[Simular mock] Would send text message{contact_info} "
                            f"via simulang sms: URL → macOS Messages",
        _TASK_OPEN_EMAIL: "[Simular mock] Would open email via simulang "
                          "App.defaultBrowser().open('https://mail.google.com')",
        _TASK_OPEN_PORTAL: "[Simular mock] Would open patient portal via simulang "
                           "App.defaultBrowser().open('https://mychart.org')",
        _TASK_PLAY_MEDIA: f"[Simular mock] Would open media player via simulang — plan: {plan}",
        _TASK_OPEN_WEBSITE: f"[Simular mock] Would navigate/search browser via simulang — plan: {plan}",
        _TASK_GENERAL_NAV: f"[Simular mock] Would perform web navigation via simulang — plan: {plan}",
    }
    return details.get(task_type, f"[Simular mock] Would: {plan}")
