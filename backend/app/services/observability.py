"""Observability via Arize (§3.7).

CONTRACT: `log_triage(...)` is fire-and-forget — it must NEVER raise into the
request pipeline. The observability feature subagent implements real Arize
logging; this scaffold logs to stdout.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from ..config import get_settings
from ..models import TriageResult

settings = get_settings()
logger = logging.getLogger("tendly.observability")


def log_triage(
    *,
    transcript: str,
    patient_context: str,
    result: TriageResult,
    latency_ms: float,
    patient_id: str,
    session_id: Optional[str] = None,
) -> None:
    """Log a triage decision. Fire-and-forget; swallow all errors."""
    try:
        logger.info(
            "TRIAGE patient=%s category=%s urgency=%s latency=%.0fms reasoning=%s",
            patient_id, result.category, result.urgency, latency_ms, result.reasoning,
        )
        # Real Arize logging implemented by the observability feature.
    except Exception:  # pragma: no cover - never break the pipeline
        pass


class Timer:
    """Convenience context manager to measure triage latency in ms."""

    def __enter__(self) -> "Timer":
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc) -> None:
        self.elapsed_ms = (time.perf_counter() - self._start) * 1000.0
