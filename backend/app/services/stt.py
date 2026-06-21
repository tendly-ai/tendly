"""Speech-to-text via Deepgram (§3.1.2).

CONTRACT: `transcribe(audio_bytes, content_type) -> str`. The AI-triage/voice
feature subagent implements the real Deepgram call. Mock returns a placeholder
so the pipeline runs without audio.
"""
from __future__ import annotations

import logging

import httpx

from ..config import get_settings

settings = get_settings()
logger = logging.getLogger("tendly.stt")


async def transcribe(audio_bytes: bytes, content_type: str = "audio/webm") -> str:
    """Transcribe audio to text. Returns the transcript string."""
    if not settings.deepgram_api_key:
        if settings.allow_mocks:
            return "[mock transcript — Deepgram key not configured]"
        raise RuntimeError("DEEPGRAM_API_KEY not configured")

    from deepgram import DeepgramClient, PrerecordedOptions  # type: ignore

    try:
        client = DeepgramClient(settings.deepgram_api_key)
        options = PrerecordedOptions(model="nova-2", smart_format=True)
        source = {"buffer": audio_bytes, "mimetype": content_type}
        resp = await client.listen.asyncprerecorded.v("1").transcribe_file(
            source, options
        )
        transcript: str = (
            resp.results.channels[0].alternatives[0].transcript
        )
        if not transcript:
            logger.warning("Deepgram returned empty transcript")
            return ""
        return transcript
    except Exception:
        logger.exception("Deepgram transcription failed — falling back to mock")
        if settings.allow_mocks:
            return "[mock transcript — Deepgram call failed, using fallback]"
        raise


async def synthesize(text: str) -> bytes:
    """Turn response text into speech audio using Deepgram TTS."""
    clean_text = " ".join(text.split())
    if not clean_text:
        raise ValueError("text is required")

    if not settings.deepgram_api_key:
        if settings.allow_mocks:
            return b""
        raise RuntimeError("DEEPGRAM_API_KEY not configured")

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                "https://api.deepgram.com/v1/speak",
                params={"model": "aura-2-thalia-en", "encoding": "mp3"},
                headers={
                    "Authorization": f"Token {settings.deepgram_api_key}",
                    "Content-Type": "application/json",
                },
                json={"text": clean_text},
            )
            resp.raise_for_status()
            return resp.content
    except Exception:
        logger.exception("Deepgram TTS failed")
        if settings.allow_mocks:
            return b""
        raise
