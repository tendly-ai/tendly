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
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")
    redis_url: str = os.getenv("REDIS_URL", "")
    arize_api_key: str = os.getenv("ARIZE_API_KEY", "")
    arize_space_id: str = os.getenv("ARIZE_SPACE_ID", "")
    sentry_dsn: str = os.getenv("SENTRY_DSN", "")
    allow_mocks: bool = _bool("TENDLY_ALLOW_MOCKS", True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
