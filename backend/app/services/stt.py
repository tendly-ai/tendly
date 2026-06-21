"""Speech-to-text via Deepgram (§3.1.2).

CONTRACT: `transcribe(audio_bytes, content_type) -> str`. The AI-triage/voice
feature subagent implements the real Deepgram call. Mock returns a placeholder
so the pipeline runs without audio.
"""
from __future__ import annotations

from ..config import get_settings

settings = get_settings()


async def transcribe(audio_bytes: bytes, content_type: str = "audio/webm") -> str:
    """Transcribe audio to text. Returns the transcript string."""
    if not settings.deepgram_api_key:
        if settings.allow_mocks:
            return "[mock transcript — Deepgram key not configured]"
        raise RuntimeError("DEEPGRAM_API_KEY not configured")

    # Real implementation (Deepgram batch). Implemented by the voice feature.
    from deepgram import DeepgramClient, PrerecordedOptions  # type: ignore

    client = DeepgramClient(settings.deepgram_api_key)
    options = PrerecordedOptions(model="nova-2", smart_format=True)
    source = {"buffer": audio_bytes, "mimetype": content_type}
    resp = client.listen.prerecorded.v("1").transcribe_file(source, options)
    return resp.results.channels[0].alternatives[0].transcript
