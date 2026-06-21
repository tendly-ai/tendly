"""Observability via Arize (§3.7).

CONTRACT: `log_triage(...)` is fire-and-forget — it must NEVER raise into the
request pipeline. When ARIZE_API_KEY and ARIZE_SPACE_ID are set, logs triage
decisions to Arize; otherwise logs to stdout only.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from ..config import get_settings
from ..models import TriageResult

settings = get_settings()
logger = logging.getLogger("tendly.observability")

# Lazy-init Arize client (no-op when creds are missing)
_arize_client = None


def _get_arize_client():
    """Return the Arize Client singleton, or None if creds are absent."""
    global _arize_client
    if _arize_client is not None:
        return _arize_client
    if not settings.arize_api_key or not settings.arize_space_id:
        return None
    try:
        from arize.api import Client

        _arize_client = Client(
            api_key=settings.arize_api_key,
            space_id=settings.arize_space_id,
        )
        logger.info("Arize client initialized")
        return _arize_client
    except Exception:
        logger.warning("Failed to initialize Arize client", exc_info=True)
        return None


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
        # Always log to stdout regardless of Arize availability
        logger.info(
            "TRIAGE patient=%s category=%s urgency=%s latency=%.0fms reasoning=%s",
            patient_id,
            result.category,
            result.urgency,
            latency_ms,
            result.reasoning,
        )

        # Attempt Arize logging (no-op when creds are missing)
        client = _get_arize_client()
        if client is None:
            return

        import pandas as pd
        from arize.utils.types import Environments, ModelTypes, Schema

        prediction_id = str(uuid4())
        now = datetime.now(timezone.utc)

        df = pd.DataFrame(
            [
                {
                    "prediction_id": prediction_id,
                    "prediction_label": result.category.value,
                    "transcript": transcript,
                    "patient_context": patient_context,
                    "patient_id": patient_id,
                    "session_id": session_id or "",
                    "latency_ms": latency_ms,
                    "urgency": result.urgency.value,
                    "summary": result.summary,
                    "reasoning": result.reasoning,
                    "timestamp": now,
                }
            ]
        )

        schema = Schema(
            prediction_id_column_name="prediction_id",
            timestamp_column_name="timestamp",
            prediction_label_column_name="prediction_label",
            feature_column_names=["transcript", "patient_context"],
            tag_column_names=[
                "patient_id",
                "session_id",
                "latency_ms",
                "urgency",
                "summary",
                "reasoning",
            ],
        )

        response = client.log(
            dataframe=df,
            schema=schema,
            model_id="tendly-triage",
            model_version="0.1.0",
            model_type=ModelTypes.SCORE_CATEGORICAL,
            environment=Environments.PRODUCTION,
        )

        if response.status_code != 200:
            logger.warning("Arize log returned status %s", response.status_code)

    except Exception:
        # Never break the pipeline
        logger.debug("Arize logging failed (non-critical)", exc_info=True)


class Timer:
    """Convenience context manager to measure triage latency in ms."""

    def __enter__(self) -> "Timer":
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc) -> None:
        self.elapsed_ms = (time.perf_counter() - self._start) * 1000.0
