"""Extract role-inference timeline rows from NeMo agent logs.

Use this after a replay or live consultation when the UI labels moved and you
need to see which role decision caused it. The script reads Docker log text
from stdin, keeps only the requested session, and writes compact JSONL rows for
accepted updates, suppressed flips, fallback paths, and the final mapping.

With `--quality-json` it also appends a `role_timeline.quality_check` row that
compares the session.quality flip counters against the log-derived counts:
quality counters are snapshotted at disconnect, so role decisions made while
the tail batch drains land only in the logs and would otherwise go unnoticed.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

ROLE_TIMELINE_EVENTS = (
    "role_inference.completed",
    "role_inference.empty_result",
    "role_inference.tool_not_invoked",
    "role_inference.truncated",
    "role_mapping.flip_detected",
    "role_mapping.flip_suppressed",
)


@dataclass
class RoleTimelineSummary:
    """Running summary for one browser session's role decisions.

    The final JSONL row uses this to show whether the browser saw fallback
    role labels, accepted flips, or held flips during the visit. Empty mapping
    means no role update was visible before the logs ended.
    """

    session_id: str
    final_mapping: dict[str, str] = field(default_factory=dict)
    role_calls: int = 0
    accepted_flips: int = 0
    suppressed_flips: int = 0
    fallback_events: int = 0
    truncation_events: int = 0

    def observe(self, timeline_row: dict[str, Any]) -> None:
        """Fold one timeline row into the final visit summary.

        Args:
            timeline_row: Normalized role event; empty mapping means the UI did not change labels.
        """
        event_name = str(timeline_row.get("event", ""))

        # Completed role calls are the decisions that can relabel the transcript UI.
        if event_name == "role_inference.completed":
            self.role_calls += 1

        # The latest non-empty mapping is the label state the user last saw.
        if isinstance(timeline_row.get("mapping"), dict) and timeline_row["mapping"]:
            self.final_mapping = dict(timeline_row["mapping"])

        # Count the state-change log only, so completed-call logs do not double-count flips.
        if event_name == "role_mapping.flip_detected":
            self.accepted_flips += 1

        # Suppressed flips are deliberate non-updates while the speaker labels settle.
        if event_name == "role_mapping.flip_suppressed":
            self.suppressed_flips += 1

        # Fallback means the UI used non-agent role evidence for this update.
        if timeline_row.get("fallback") is True:
            self.fallback_events += 1

        # Truncation explains why the UI may have stayed on raw speaker labels.
        if event_name == "role_inference.truncated":
            self.truncation_events += 1

    def to_row(self) -> dict[str, Any]:
        """Return the final summary row for the JSONL artifact.

        Returns:
            Summary object; empty final mapping means no role update was logged.
        """
        return {
            "event": "role_timeline.summary",
            "session_id": self.session_id,
            "final_mapping": self.final_mapping,
            "role_calls": self.role_calls,
            "accepted_flips": self.accepted_flips,
            "suppressed_flips": self.suppressed_flips,
            "fallback_events": self.fallback_events,
            "truncation_events": self.truncation_events,
        }


def parse_args() -> argparse.Namespace:
    """Parse the session ID whose role timeline should be extracted.

    Returns:
        CLI namespace; a missing `--quality-json` means no agreement check is emitted.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_id")
    parser.add_argument("--quality-json", type=Path, default=None)
    return parser.parse_args()


def json_from_docker_log_line(line: str) -> dict[str, Any] | None:
    """Parse one Docker log line into a JSON object when possible.

    Args:
        line: Raw line from `docker compose logs`; empty or plain text means no JSON event.

    Returns:
        Log event dict, or None when the line is not structured agent JSON.
    """
    start_index = line.find("{")

    # Plain console logs have no JSON object for the timeline to inspect.
    if start_index < 0:
        return None

    try:
        parsed = json.loads(line[start_index:])
    except json.JSONDecodeError:
        return None

    # Non-object JSON cannot carry role event fields.
    if not isinstance(parsed, dict):
        return None

    return parsed


def canonical_role_event_name(raw_event_name: str) -> str | None:
    """Return the stable role event name from a rendered log message.

    Args:
        raw_event_name: Logger message text; empty means no event was recorded.

    Returns:
        Stable event name, or None when this log line is not part of role decisions.
    """
    # Flip logs include rendered speaker IDs after the event name.
    for event_name in ROLE_TIMELINE_EVENTS:
        if raw_event_name.startswith(event_name):
            return event_name
    return None


def timeline_row_for_event(
    event: dict[str, Any],
    session_id: str,
) -> dict[str, Any] | None:
    """Normalize one structured log event for a browser session timeline.

    Args:
        event: JSON log event from the agent; missing session ID means it cannot be scoped.
        session_id: Browser recording UUID to keep; other sessions are ignored.

    Returns:
        Compact timeline row, or None when the event is unrelated to this visit.
    """
    event_name = canonical_role_event_name(str(event.get("event", "")))

    # Non-role logs are ignored so the artifact stays focused on speaker labels.
    if event_name is None:
        return None

    # Session-scoped timelines must not mix decisions from another browser visit.
    if event.get("session_id") != session_id:
        return None

    row: dict[str, Any] = {
        "ts": event.get("ts"),
        "event": event_name,
        "session_id": session_id,
    }

    for field_name in (
        "mapping",
        "previous",
        "proposed",
        "current",
        "confidence",
        "running_confidence",
        "pending_flip_count",
        "flip_detected",
        "tool_invoked",
        "fallback",
        "path",
        "role_truncation_events",
    ):
        # Missing fields are normal across role events and older log rows.
        if field_name in event:
            row[field_name] = event[field_name]

    # Suppressed flips mean the user kept the prior labels while evidence settled.
    if event_name == "role_mapping.flip_suppressed":
        row["decision"] = "suppressed_flip"
    # Accepted flip rows are the state changes that can relabel existing transcript cards.
    elif event_name == "role_mapping.flip_detected":
        row["decision"] = "accepted_flip"
        row["mapping"] = event.get("current", {})
    # Completed role calls show the mapping or fallback path that reached the browser.
    elif event_name == "role_inference.completed":
        row["decision"] = _decision_for_completed_role_call(event)
    # Tool misses explain why the UI may have used fallback labels or stayed raw.
    elif event_name == "role_inference.tool_not_invoked":
        row["decision"] = "tool_not_invoked"
    # Truncation rows explain raw or stale labels caused by model output limits.
    elif event_name == "role_inference.truncated":
        row["decision"] = "truncated"
    # Empty results are retained so a missing UI update has a visible reason.
    else:
        row["decision"] = "empty_result"

    return row


def _decision_for_completed_role_call(event: dict[str, Any]) -> str:
    """Classify a completed role call by what the browser would have seen.

    Args:
        event: `role_inference.completed` log row; missing fields mean older logs.

    Returns:
        Decision label for the timeline artifact.
    """
    # Fallback paths mean the UI did not receive a model-tool role decision.
    if event.get("fallback") is True:
        return "fallback"

    # Accepted flips are visible relabels and need to stand out in the artifact.
    if event.get("flip_detected") is True:
        return "accepted_flip"

    return "accepted_update"


def timeline_rows_from_logs(
    log_lines: TextIO,
    session_id: str,
) -> list[dict[str, Any]]:
    """Extract all timeline rows for one browser session.

    Args:
        log_lines: Docker log stream; empty means only a summary row is returned.
        session_id: Browser recording UUID whose role decisions should be kept.

    Returns:
        Timeline rows followed by a summary row for the requested session.
    """
    rows: list[dict[str, Any]] = []
    summary = RoleTimelineSummary(session_id=session_id)

    # Each Docker line may contain one structured JSON process event.
    for line in log_lines:
        event = json_from_docker_log_line(line)

        # Plain log lines are expected when LOG_FORMAT=console or Docker adds noise.
        if event is None:
            continue

        row = timeline_row_for_event(event, session_id)

        # Unrelated structured logs stay out of the role timeline.
        if row is None:
            continue

        rows.append(row)
        summary.observe(row)

    rows.append(summary.to_row())
    return rows


def quality_check_row(
    timeline_rows: list[dict[str, Any]],
    quality: dict[str, Any],
    session_id: str,
) -> dict[str, Any]:
    """Compare session.quality flip counters with the log-derived counts.

    Args:
        timeline_rows: Timeline rows including the trailing summary row.
        quality: Parsed session.quality record; missing counters count as zero.
        session_id: Browser recording UUID the check belongs to.

    Returns:
        Agreement row; `flip_counts_match` false means the quality record was
        snapshotted before some role decisions (they exist only in the logs).
    """
    summary = timeline_rows[-1] if timeline_rows else {}
    quality_accepted = int(quality.get("role_flips_accepted", 0))
    quality_suppressed = int(quality.get("role_flips_suppressed", 0))
    timeline_accepted = int(summary.get("accepted_flips", 0))
    timeline_suppressed = int(summary.get("suppressed_flips", 0))
    finalized_at = str(quality.get("finalized_at", ""))

    # Decisions logged after the disconnect snapshot explain most count mismatches.
    events_after_snapshot = 0
    if finalized_at:
        events_after_snapshot = sum(
            1
            for row in timeline_rows
            if row.get("event") != "role_timeline.summary"
            and str(row.get("ts", "")) > finalized_at
        )

    return {
        "event": "role_timeline.quality_check",
        "session_id": session_id,
        "quality_flips_accepted": quality_accepted,
        "quality_flips_suppressed": quality_suppressed,
        "timeline_flips_accepted": timeline_accepted,
        "timeline_flips_suppressed": timeline_suppressed,
        "flip_counts_match": (
            quality_accepted == timeline_accepted
            and quality_suppressed == timeline_suppressed
        ),
        "role_events_after_quality_snapshot": events_after_snapshot,
        "quality_finalized_at": finalized_at or None,
    }


def main() -> int:
    """Read Docker logs from stdin and write a JSONL role timeline.

    Returns:
        Process exit code; `0` means the artifact was written, even if no role events existed.
    """
    args = parse_args()
    rows = timeline_rows_from_logs(sys.stdin, args.session_id)

    # The optional agreement row makes snapshot-vs-log counter drift visible per run.
    if args.quality_json is not None:
        quality = json.loads(args.quality_json.read_text(encoding="utf-8"))
        rows.append(quality_check_row(rows, quality, args.session_id))

    # JSONL keeps long fixture runs stream-readable and easy to grep by event.
    for row in rows:
        print(json.dumps(row, sort_keys=True))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
