"""Extract per-window speaker-continuity rows from NeMo agent logs.

Use this after a replay or live consultation when a Doctor/Patient row looks
wrong and you need to see which emission window produced it and how its raw
speaker IDs were mapped to the visible canonical IDs. The script reads Docker
log text from stdin, keeps only the requested session's
`nemo_session.window_continuity` events, and writes compact JSONL rows plus a
summary. Rows carry speaker IDs, timings, votes, and counts - never transcript
text - so the artifact is safe to keep with eval runs.

Requires the agent container to run with LOG_FORMAT=json; the console format
drops the structured fields this artifact is built from.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Any, TextIO

WINDOW_CONTINUITY_EVENT = "nemo_session.window_continuity"

_WINDOW_ROW_FIELDS = (
    "window_index",
    "phase",
    "window_start_seconds",
    "buffer_end_seconds",
    "emitted_from_seconds",
    "emitted_until_seconds",
    "raw_speaker_ids",
    "known_speaker_ids",
    "canonical_speaker_ids",
    "speaker_id_map",
    "overlap_votes",
    "mapping_reasons",
    "window_remaps",
    "window_phantom_merges",
    "slot_share_evidence",
    "folded_word_spans",
    "cumulative_anchor_remaps",
    "cumulative_phantom_merges",
    "emitted_rows",
    "held_rows",
)


@dataclass
class WindowContinuitySummary:
    """Running totals for one session's emission windows.

    The final JSONL row uses this so an eval run can see at a glance how many
    windows ran, how many rows the clinician received, and how often speaker
    identity was remapped or merged at window seams.
    """

    session_id: str
    window_count: int = 0
    emitted_rows: int = 0
    held_rows: int = 0
    windows_with_remaps: int = 0
    window_remaps: int = 0
    window_phantom_merges: int = 0

    def observe(self, window_row: dict[str, Any]) -> None:
        """Fold one window row into the session summary.

        Args:
            window_row: Normalized continuity record; zero counts mean a quiet window.
        """
        self.window_count += 1
        self.emitted_rows += int(window_row.get("emitted_rows", 0) or 0)
        self.held_rows += int(window_row.get("held_rows", 0) or 0)
        remaps = int(window_row.get("window_remaps", 0) or 0)
        self.window_remaps += remaps

        # Remapped windows are where visible speaker identity was corrected mid-session.
        if remaps > 0:
            self.windows_with_remaps += 1

        self.window_phantom_merges += int(
            window_row.get("window_phantom_merges", 0) or 0
        )

    def to_row(self) -> dict[str, Any]:
        """Return the trailing summary row for the JSONL artifact.

        Returns:
            Summary object; zero windows means the session logged no continuity rows.
        """
        return {
            "event": "window_continuity.summary",
            "session_id": self.session_id,
            "window_count": self.window_count,
            "emitted_rows": self.emitted_rows,
            "held_rows": self.held_rows,
            "windows_with_remaps": self.windows_with_remaps,
            "window_remaps": self.window_remaps,
            "window_phantom_merges": self.window_phantom_merges,
        }


def parse_args() -> argparse.Namespace:
    """Parse the session ID whose window artifact should be extracted.

    Returns:
        CLI namespace containing the browser recording UUID.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_id")
    return parser.parse_args()


def json_from_docker_log_line(line: str) -> dict[str, Any] | None:
    """Parse one Docker log line into a JSON object when possible.

    Args:
        line: Raw line from `docker compose logs`; plain text means no JSON event.

    Returns:
        Log event dict, or None when the line is not structured agent JSON.
    """
    start_index = line.find("{")

    # Plain console logs have no JSON object for the artifact to inspect.
    if start_index < 0:
        return None

    try:
        parsed = json.loads(line[start_index:])
    except json.JSONDecodeError:
        return None

    # Non-object JSON cannot carry window continuity fields.
    if not isinstance(parsed, dict):
        return None

    return parsed


def window_row_for_event(
    event: dict[str, Any],
    session_id: str,
) -> dict[str, Any] | None:
    """Normalize one structured log event into a window-continuity row.

    Args:
        event: JSON log event from the agent; missing session ID cannot be scoped.
        session_id: Browser recording UUID to keep; other sessions are ignored.

    Returns:
        Compact continuity row, or None when the event is unrelated to this visit.
    """
    # The rendered message starts with the event name followed by log args.
    if not str(event.get("event", "")).startswith(WINDOW_CONTINUITY_EVENT):
        return None

    # Session-scoped artifacts must not mix windows from another browser visit.
    if event.get("session_id") != session_id:
        return None

    row: dict[str, Any] = {
        "ts": event.get("ts"),
        "event": WINDOW_CONTINUITY_EVENT,
        "session_id": session_id,
    }

    # Missing fields are normal for older log rows; joins treat them as unknown.
    for field_name in _WINDOW_ROW_FIELDS:
        if field_name in event:
            row[field_name] = event[field_name]

    return row


def window_rows_from_logs(
    log_lines: TextIO,
    session_id: str,
) -> list[dict[str, Any]]:
    """Extract all window-continuity rows for one browser session.

    Args:
        log_lines: Docker log stream; empty means only a summary row is returned.
        session_id: Browser recording UUID whose windows should be kept.

    Returns:
        Window rows in log order followed by a summary row.
    """
    rows: list[dict[str, Any]] = []
    summary = WindowContinuitySummary(session_id=session_id)

    # Each Docker line may contain one structured JSON process event.
    for line in log_lines:
        event = json_from_docker_log_line(line)

        # Plain log lines are expected when Docker adds noise around JSON.
        if event is None:
            continue

        row = window_row_for_event(event, session_id)

        # Unrelated structured logs stay out of the window artifact.
        if row is None:
            continue

        rows.append(row)
        summary.observe(row)

    rows.append(summary.to_row())
    return rows


def main() -> int:
    """Read Docker logs from stdin and write a JSONL window artifact.

    Returns:
        Process exit code; `0` means the artifact was written, even when the
        session logged no window rows (the summary row still records that).
    """
    args = parse_args()
    rows = window_rows_from_logs(sys.stdin, args.session_id)

    # JSONL keeps long fixture runs stream-readable and easy to join by window.
    for row in rows:
        print(json.dumps(row, sort_keys=True))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
