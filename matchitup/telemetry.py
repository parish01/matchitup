"""Tracing helpers.

Each agent step is timed and recorded in the card's receipt (shown in the
UI). If ``APPLICATIONINSIGHTS_CONNECTION_STRING`` is set and the
``azure-monitor-opentelemetry`` package is installed, the same steps are
exported as OpenTelemetry spans to Azure Application Insights.
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from typing import Any, Iterator

log = logging.getLogger("matchitup")
_tracer = None


def setup() -> str:
    global _tracer
    conn = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not conn:
        return "disabled"
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
        from opentelemetry import trace

        configure_azure_monitor(connection_string=conn)
        _tracer = trace.get_tracer("matchitup")
        return "application_insights"
    except Exception as exc:  # pragma: no cover - optional dependency
        log.warning("Telemetry disabled: %s", exc)
        return "disabled"


@contextmanager
def step(steps: list[dict[str, Any]], agent: str, **attrs: Any) -> Iterator[dict[str, Any]]:
    """Time an agent step, append it to ``steps`` and mirror it as a span when enabled."""
    record: dict[str, Any] = {"agent": agent, **attrs}
    start = time.perf_counter()
    span_cm = _tracer.start_as_current_span(f"agent.{agent}") if _tracer else None
    span = span_cm.__enter__() if span_cm else None
    try:
        yield record
    finally:
        record["ms"] = round((time.perf_counter() - start) * 1000, 1)
        steps.append(record)
        if span is not None:
            for k, v in record.items():
                if isinstance(v, (str, int, float, bool)):
                    span.set_attribute(f"matchitup.{k}", v)
            span_cm.__exit__(None, None, None)
