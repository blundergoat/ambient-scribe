#!/usr/bin/env python3
"""
Analyze Ambient Scribe JSON-line process logs.

Use this after a local `/scribe` run to join PHP and FastAPI events by
`session_id` and `correlation_id`. The script tolerates non-JSON Docker log
lines and prints latency, token, role-quality, and Mercure reliability signals.
It never needs transcript text, audio, prompts, summaries, or model responses.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any, Iterable, TextIO


def iter_json_events(paths: list[Path]) -> Iterable[dict[str, Any]]:
    """Yield JSON log objects from files or stdin.

    Args:
        paths: Log files to read; empty means stdin, which is useful for Docker logs.

    Yields:
        Parsed log objects; non-JSON lines are ignored because Docker prefixes can be mixed in.
    """
    # No file arguments means the operator piped Docker output into the script.
    if paths == []:
        yield from _iter_json_events_from_stream(sys.stdin)
        return

    # Each file may contain mixed service logs from app, FastAPI, and eval scripts.
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            yield from _iter_json_events_from_stream(handle)


def _iter_json_events_from_stream(handle: TextIO) -> Iterable[dict[str, Any]]:
    """Parse JSON lines from one stream while skipping plain text.

    Args:
        handle: Open text stream; empty lines mean no process event.

    Yields:
        JSON objects with string keys; malformed or array lines are skipped.
    """
    # Each log line is independent so one malformed line cannot hide the rest of the run.
    for raw_line in handle:
        line = raw_line.strip()
        # Blank Docker lines carry no process signal.
        if line == "":
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        # Only JSON objects match the canonical log schema.
        if isinstance(parsed, dict):
            yield parsed


def percentile(values: list[float], percent: float) -> float:
    """Return a nearest-rank percentile for a latency series.

    Args:
        values: Numeric samples; empty means no event emitted that metric.
        percent: Percentile in the range 0-100.

    Returns:
        Percentile value, or 0.0 when no samples were present.
    """
    # Empty buckets are expected when a local run exercises only part of the UI.
    if values == []:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((percent / 100) * (len(ordered) - 1))))
    return ordered[index]


def numeric(event: dict[str, Any], field: str) -> float | None:
    """Read a numeric field from a log event.

    Args:
        event: Parsed log event; missing fields mean this event does not feed that metric.
        field: Metric field name to read.

    Returns:
        Float value for aggregation, or `None` when the field is absent or non-numeric.
    """
    value = event.get(field)
    # Booleans are numeric in Python but are status flags, not measurements.
    if isinstance(value, bool):
        return None
    # Integers and floats become samples for percentile or mean calculations.
    if isinstance(value, int | float):
        return float(value)
    return None


@dataclass
class ProcessQualityStats:
    """
    Aggregates the log fields an operator checks after using `/scribe`.

    The analyzer fills this once from JSON logs, then renders latency, token,
    role-quality, reliability, and per-session summaries. Empty fields mean that
    workflow was not exercised in the captured logs.

    Attributes:
        sessions: Events grouped by browser session.
        latency_buckets: Duration samples by process metric name.
        role_paths: Count of tool, free-text, heuristic, or none role paths.
        mercure_outcomes: Success/retry/fail/skip counts for browser delivery.
        php_statuses: HTTP status mix for Symfony-to-Python calls.
        json_failures: Model JSON parse failure counts.
        eval_accuracy: Scenario accuracy samples from eval JSON lines.
        confidence_values: Role confidence samples shown in the UI.
        tool_success_rates: Strands tool success rates when emitted.
        cycles: Strands event-loop cycle counts.
    """

    sessions: dict[str, list[dict[str, Any]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    latency_buckets: dict[str, list[float]] = field(
        default_factory=lambda: defaultdict(list)
    )
    role_paths: Counter[str] = field(default_factory=Counter)
    mercure_outcomes: Counter[str] = field(default_factory=Counter)
    php_statuses: Counter[str] = field(default_factory=Counter)
    json_failures: Counter[str] = field(default_factory=Counter)
    eval_accuracy: list[float] = field(default_factory=list)
    confidence_values: list[float] = field(default_factory=list)
    tool_success_rates: list[float] = field(default_factory=list)
    cycles: list[float] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_total: int = 0
    fallback_count: int = 0
    role_completed: int = 0
    flip_count: int = 0


def build_report(events: list[dict[str, Any]]) -> list[str]:
    """Build the process-quality report lines.

    Args:
        events: Parsed JSON log objects from one or more services; empty produces a zero-signal report.

    Returns:
        Human-readable report lines for the operator.
    """
    stats = ProcessQualityStats()
    # One pass keeps the script useful for large Docker log captures.
    for event in events:
        _record_event(stats, event)

    return _render_report(stats, len(events))


def _record_event(stats: ProcessQualityStats, event: dict[str, Any]) -> None:
    """Add one JSON log object to the report totals.

    Args:
        stats: Mutable report state for the current log capture.
        event: Parsed log object; empty objects contribute only to the event count.
    """
    _record_session(stats, event)
    event_name = str(event.get("event", ""))
    _record_latency_and_usage(stats, event_name, event)
    _record_role_quality(stats, event_name, event)
    _record_reliability(stats, event_name, event)
    _record_eval(stats, event_name, event)


def _record_session(stats: ProcessQualityStats, event: dict[str, Any]) -> None:
    """Group a log event by browser session when one is present.

    Args:
        stats: Report state that owns the per-session event map.
        event: Parsed log object; missing session ID means the line is process-scoped.
    """
    session_id = event.get("session_id")
    # Session grouping is the main way support follows one browser recording.
    if isinstance(session_id, str) and session_id != "":
        stats.sessions[session_id].append(event)


def _record_latency_and_usage(
    stats: ProcessQualityStats,
    event_name: str,
    event: dict[str, Any],
) -> None:
    """Capture duration, token, and cycle fields from one event.

    Args:
        stats: Report state that owns latency and cost buckets.
        event_name: Stable event slug; empty means the line has no known metric mapping.
        event: Parsed log object that may contain numeric fields.
    """
    # WebSocket chunk logs expose both NeMo inference time and end-to-end chunk time.
    if event_name == "websocket.chunk_e2e":
        _add_numeric(
            stats.latency_buckets["websocket.chunk_e2e.inference_ms"],
            event,
            "inference_ms",
        )
        _add_numeric(
            stats.latency_buckets["websocket.chunk_e2e.total_ms"], event, "total_ms"
        )

    # Role and summary completions carry app duration and SDK model duration.
    if event_name in {"role_inference.completed", "summary.completed"}:
        _add_numeric(
            stats.latency_buckets[f"{event_name}.duration_ms"], event, "duration_ms"
        )
        _add_numeric(
            stats.latency_buckets[f"{event_name}.model_latency_ms"],
            event,
            "model_latency_ms",
        )
        stats.tokens_in += int(event.get("tokens_in", 0) or 0)
        stats.tokens_out += int(event.get("tokens_out", 0) or 0)
        stats.tokens_total += int(event.get("tokens_total", 0) or 0)
        _add_numeric(stats.cycles, event, "cycles")


def _record_role_quality(
    stats: ProcessQualityStats,
    event_name: str,
    event: dict[str, Any],
) -> None:
    """Capture role path, confidence, fallback, and flip fields.

    Args:
        stats: Report state that owns role-quality counters.
        event_name: Stable event slug; non-role events are ignored.
        event: Parsed log object for one role operation.
    """
    # Non-role events do not affect the visible speaker-label quality report.
    if event_name != "role_inference.completed":
        return

    stats.role_completed += 1
    stats.role_paths[str(event.get("path", "unknown"))] += 1
    # Fallback means heuristic or no-result handling, not the Strands tool path.
    if event.get("fallback") is True:
        stats.fallback_count += 1
    # Role flips explain visible relabeling in the transcript.
    if event.get("flip_detected") is True:
        stats.flip_count += 1
    _add_numeric(stats.confidence_values, event, "confidence")
    _add_numeric(stats.tool_success_rates, event, "tool_success_rate")


def _record_reliability(
    stats: ProcessQualityStats,
    event_name: str,
    event: dict[str, Any],
) -> None:
    """Capture delivery and proxy reliability fields.

    Args:
        stats: Report state that owns reliability counters.
        event_name: Stable event slug; empty means no reliability bucket matches.
        event: Parsed log object that may contain status fields.
    """
    # Mercure outcomes show whether the browser received real-time updates.
    if event_name.startswith("mercure.publish."):
        stats.mercure_outcomes[event_name.removeprefix("mercure.publish.")] += 1

    # JSON parse failures show model-output quality problems without logging the response.
    if event_name in {"role_inference.no_json_found", "summary.no_json_found"}:
        stats.json_failures[event_name] += 1

    # PHP client calls show proxy reliability and status mix.
    if event_name == "strands.client.call":
        stats.php_statuses[str(event.get("status", "unknown"))] += 1


def _record_eval(
    stats: ProcessQualityStats, event_name: str, event: dict[str, Any]
) -> None:
    """Capture role heuristic eval JSON lines.

    Args:
        stats: Report state that owns eval accuracy samples.
        event_name: Stable event slug; non-eval lines are ignored.
        event: Parsed log object that may contain `accuracy`.
    """
    # Evaluation JSON lines can be piped straight into this analyzer.
    if event_name == "eval.role_heuristic" and isinstance(
        event.get("accuracy"), int | float
    ):
        stats.eval_accuracy.append(float(event["accuracy"]))


def _render_report(stats: ProcessQualityStats, event_count: int) -> list[str]:
    """Render process-quality totals for the operator.

    Args:
        stats: Aggregated process-quality fields from JSON logs.
        event_count: Number of parsed JSON objects; zero means the capture had no structured logs.

    Returns:
        Report lines for latency, usage, role quality, reliability, and sessions.
    """
    report = ["Ambient Scribe process-quality report", ""]
    report.append(f"Events: {event_count}")
    report.append(f"Sessions: {len(stats.sessions)}")
    report.append("")
    report.extend(_latency_lines(stats.latency_buckets))
    report.append("")
    report.append("Cost / usage")
    report.append(
        f"  tokens_total={stats.tokens_total} tokens_in={stats.tokens_in} tokens_out={stats.tokens_out}"
    )
    report.append(
        f"  mean_cycles={_format_float(mean(stats.cycles) if stats.cycles else 0.0)}"
    )
    report.append("")
    report.append("Role quality")
    report.append(f"  paths={dict(stats.role_paths)}")
    report.append(
        f"  fallback_rate={_format_rate(stats.fallback_count, stats.role_completed)}"
    )
    report.append(f"  flip_rate={_format_rate(stats.flip_count, stats.role_completed)}")
    report.append(
        f"  mean_confidence={_format_float(mean(stats.confidence_values) if stats.confidence_values else 0.0)}"
    )
    report.append(
        f"  mean_tool_success_rate={_format_float(mean(stats.tool_success_rates) if stats.tool_success_rates else 0.0)}"
    )
    report.append("")
    report.append("Reliability")
    report.append(f"  mercure={dict(stats.mercure_outcomes)}")
    report.append(f"  json_parse_failures={dict(stats.json_failures)}")
    report.append(f"  php_statuses={dict(stats.php_statuses)}")

    # Evaluation accuracy appears only when eval-role-heuristic output is included.
    if stats.eval_accuracy:
        report.append("")
        report.append(
            f"Heuristic eval accuracy={_format_float(mean(stats.eval_accuracy))}"
        )

    report.append("")
    report.append("Per session")
    # Each session one-liner helps support find the outlier recording quickly.
    for session_id, session_events in sorted(stats.sessions.items()):
        report.append(_session_line(session_id, session_events))

    return report


def _add_numeric(bucket: list[float], event: dict[str, Any], field: str) -> None:
    """Append a numeric log field to a metric bucket.

    Args:
        bucket: Mutable metric samples for a report section.
        event: Parsed log event that may contain the field.
        field: Field to read from the event.
    """
    value = numeric(event, field)
    # Missing fields are normal when a service emits only part of the canonical schema.
    if value is not None:
        bucket.append(value)


def _latency_lines(latency_buckets: dict[str, list[float]]) -> list[str]:
    """Format latency percentile rows.

    Args:
        latency_buckets: Named latency samples; empty buckets are omitted.

    Returns:
        Report lines for count, p50, p95, and max.
    """
    lines = ["Latency"]
    # Stable names make repeated reports easy to compare.
    for name in sorted(latency_buckets):
        values = latency_buckets[name]
        lines.append(
            "  "
            + f"{name}: count={len(values)} p50={_format_float(percentile(values, 50))} "
            + f"p95={_format_float(percentile(values, 95))} max={_format_float(max(values) if values else 0.0)}"
        )
    # No latency lines means the capture did not include live role, summary, or WebSocket events.
    if len(lines) == 1:
        lines.append("  none")
    return lines


def _session_line(session_id: str, events: list[dict[str, Any]]) -> str:
    """Build one session summary line.

    Args:
        session_id: Browser recording ID used as the log join key.
        events: Events carrying that session ID; empty is not expected after grouping.

    Returns:
        One-line summary with event count, role paths, Mercure outcomes, and token total.
    """
    role_paths = Counter(
        str(event.get("path", "unknown"))
        for event in events
        if event.get("event") == "role_inference.completed"
    )
    mercure = Counter(
        str(event.get("event", "")).removeprefix("mercure.publish.")
        for event in events
        if str(event.get("event", "")).startswith("mercure.publish.")
    )
    tokens = sum(int(event.get("tokens_total", 0) or 0) for event in events)
    return f"  {session_id}: events={len(events)} tokens={tokens} role_paths={dict(role_paths)} mercure={dict(mercure)}"


def _format_rate(count: int, total: int) -> str:
    """Format a ratio for role-quality output.

    Args:
        count: Matching events.
        total: Total events; zero means the flow did not run.

    Returns:
        Percentage string with one decimal place.
    """
    # No total means the operator did not run that part of the workflow.
    if total == 0:
        return "0.0%"
    return f"{(count / total) * 100:.1f}%"


def _format_float(value: float) -> str:
    """Format numeric output consistently for reports.

    Args:
        value: Numeric report value; zero means no samples or no measured cost.

    Returns:
        String with two decimal places.
    """
    return f"{value:.2f}"


def main(argv: list[str] | None = None) -> int:
    """Run the log analyzer CLI.

    Args:
        argv: Optional CLI args for tests; `None` reads from the current process.

    Returns:
        Exit status; zero means the report rendered.
    """
    parser = argparse.ArgumentParser(description="Analyze Ambient Scribe JSON logs.")
    parser.add_argument(
        "paths", nargs="*", type=Path, help="JSONL log files; omit to read stdin"
    )
    args = parser.parse_args(argv)

    events = list(iter_json_events(args.paths))
    print("\n".join(build_report(events)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
