"""
Observability helpers for role and summary agent calls.

`server.py` uses these helpers to keep SDK metrics, Mercure topics, and optional
OTEL setup out of the route file. They emit only bounded process fields that help
operators understand a user's `/scribe` session without logging transcript text.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

_strands_telemetry_configured = False


def agent_metric_fields(agent_result: Any, agent_name: str) -> dict[str, Any]:
    """Extract bounded Strands SDK metrics for one role or summary run.

    Args:
        agent_result: Result returned by `Agent(...)`; missing metrics means the local mock reported none.
        agent_name: Stable agent name shown in log reports.

    Returns:
        Log fields for token, latency, cycle, and tool success metrics; zeros mean no SDK data was emitted.
    """
    metrics = getattr(agent_result, "metrics", None)
    summary = (
        metrics.get_summary()
        if metrics is not None and hasattr(metrics, "get_summary")
        else {}
    )
    usage = summary.get("accumulated_usage", {}) if isinstance(summary, dict) else {}
    model_metrics = (
        summary.get("accumulated_metrics", {}) if isinstance(summary, dict) else {}
    )
    tool_usage = summary.get("tool_usage", {}) if isinstance(summary, dict) else {}

    tool_success_rates = _tool_success_rates(tool_usage)

    return {
        "agent": agent_name,
        "tokens_in": int(usage.get("inputTokens", 0)) if isinstance(usage, dict) else 0,
        "tokens_out": int(usage.get("outputTokens", 0))
        if isinstance(usage, dict)
        else 0,
        "tokens_total": int(usage.get("totalTokens", 0))
        if isinstance(usage, dict)
        else 0,
        "model_latency_ms": int(model_metrics.get("latencyMs", 0))
        if isinstance(model_metrics, dict)
        else 0,
        "cycles": int(summary.get("total_cycles", 0))
        if isinstance(summary, dict)
        else 0,
        "tool_success_rate": (
            round(sum(tool_success_rates) / len(tool_success_rates), 4)
            if tool_success_rates
            else None
        ),
    }


def session_id_from_topic(topic: str) -> str | None:
    """Pull the browser session ID from a Mercure topic.

    Args:
        topic: Mercure topic such as `scribe/session/{id}/raw`; empty means no session can be joined.

    Returns:
        Session ID for log joins, or `None` when the topic is not session-scoped.
    """
    match = re.search(r"scribe/session/([^/]+)/", topic)
    # Non-session Mercure topics are process-scoped and cannot join a browser recording.
    if match is None:
        return None
    return match.group(1)


def configure_strands_telemetry() -> None:
    """Enable optional Strands OTEL exporters when env vars ask for them.

    With no OTEL env set, agent metrics are still captured into JSON logs.
    Console/OTLP exporters are local-development extras and never required for tests.
    """
    global _strands_telemetry_configured

    # Telemetry setup is process-wide; repeated lifespan starts should not duplicate exporters.
    if _strands_telemetry_configured:
        return

    otel_mode = os.environ.get("STRANDS_OTEL", "").lower()
    otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    # No exporter requested means JSON logs remain the only observability output.
    if otel_mode != "console" and otlp_endpoint == "":
        return

    try:
        from strands.telemetry import StrandsTelemetry

        telemetry = StrandsTelemetry().setup_meter(
            enable_console_exporter=otel_mode == "console",
            enable_otlp_exporter=otlp_endpoint != "",
        )
        # Console exporter is useful for local runs where no collector exists.
        if otel_mode == "console":
            telemetry.setup_console_exporter()
        # OTLP exporter relies on the standard endpoint env var to reach a collector.
        if otlp_endpoint != "":
            telemetry.setup_otlp_exporter()
        _strands_telemetry_configured = True
        logger.info(
            "otel.configured",
            extra={
                "exporter": "console" if otel_mode == "console" else "otlp",
            },
        )
    except Exception as error:
        logger.warning(
            "otel.configure_failed %s: %s",
            type(error).__name__,
            str(error)[:200],
            exc_info=error,
            extra={
                "error_type": type(error).__name__,
                "error": str(error)[:200],
            },
        )


def _tool_success_rates(tool_usage: Any) -> list[float]:
    """Return tool success rates from a Strands metric summary.

    Args:
        tool_usage: SDK `tool_usage` object; empty or malformed means no tool ran for the user.

    Returns:
        Tool success rates for averaging; empty means no tool metric was emitted.
    """
    rates: list[float] = []
    # Tool metrics are aggregated by tool name; only rates are safe and useful in logs.
    if not isinstance(tool_usage, dict):
        return rates

    # Each tool entry can show whether the role agent's tool path worked.
    for tool_metrics in tool_usage.values():
        execution_stats = (
            tool_metrics.get("execution_stats", {})
            if isinstance(tool_metrics, dict)
            else {}
        )
        success_rate = (
            execution_stats.get("success_rate")
            if isinstance(execution_stats, dict)
            else None
        )
        # Missing success rate means the tool did not run during this agent call.
        if isinstance(success_rate, int | float):
            rates.append(float(success_rate))

    return rates
