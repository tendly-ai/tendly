"""Central configuration. Reads from environment (.env loaded at startup)."""
from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    deepgram_api_key: str = os.getenv("DEEPGRAM_API_KEY", "")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")
    redis_url: str = os.getenv("REDIS_URL", "")
    arize_api_key: str = os.getenv("ARIZE_API_KEY", "")
    arize_space_id: str = os.getenv("ARIZE_SPACE_ID", "")
    sentry_dsn: str = os.getenv("SENTRY_DSN", "")
    allow_mocks: bool = _bool("TENDLY_ALLOW_MOCKS", True)
    allow_contact_llm_matching: bool = _bool("TENDLY_ALLOW_CONTACT_LLM_MATCHING", False)

    # Deepgram Voice Agent (browser-side agent; backend mints tokens + prompt)
    agent_listen_model: str = os.getenv("AGENT_LISTEN_MODEL", "nova-3")
    agent_think_provider: str = os.getenv("AGENT_THINK_PROVIDER", "anthropic")
    agent_think_model: str = os.getenv("AGENT_THINK_MODEL", "claude-sonnet-4-5")
    agent_speak_model: str = os.getenv("AGENT_SPEAK_MODEL", "aura-2-thalia-en")
    agent_token_ttl: int = int(os.getenv("AGENT_TOKEN_TTL", "30"))
    # Local-dev fallback: if the key can't mint short-lived tokens, send the raw
    # API key to the renderer. Acceptable for local Electron use; set False in prod.
    agent_allow_raw_key: bool = _bool("AGENT_ALLOW_RAW_KEY", True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
