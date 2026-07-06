#!/usr/bin/env python3
"""Map post-visit ASR word timings onto live transcript rows.

Use this after `probe-post-visit-timestamps.py` creates a word timing artifact.
The report shows which live row each corrected word falls into, and highlights a
target phrase such as a seam repeat. It is a QA artifact only; it does not
change correction storage, summaries, or browser payloads.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

DEFAULT_PHRASE = "let's try and get you"


def load_json(path: Path) -> dict[str, Any]:
    """Read one JSON artifact from a fixture run.

    Args:
        path: Artifact path chosen by the developer; missing or empty files fail the report.

    Returns:
        Parsed JSON object for timestamp or live-history analysis.
    """
    return json.loads(path.read_text(encoding="utf-8"))


def normalized_word(value: str) -> str:
    """Normalize one word before phrase matching.

    Args:
        value: Transcript word shown in an artifact; empty means no phrase token.

    Returns:
        Lowercase alphanumeric word; empty means the input was punctuation-only.
    """
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def normalized_phrase_words(phrase: str) -> list[str]:
    """Return normalized words for a phrase the user wants to inspect.

    Args:
        phrase: Phrase to find in corrected ASR words; empty finds nothing.

    Returns:
        Normalized word list; empty means the phrase has no searchable words.
    """
    return [word for word in (normalized_word(raw_word) for raw_word in phrase.split()) if word]


def word_center_seconds(word_timing: dict[str, Any]) -> float:
    """Return the midpoint of one estimated ASR word.

    Args:
        word_timing: Word timing row from the probe; missing times default to zero.

    Returns:
        Center time in seconds for assigning the word to a live row.
    """
    start = float(word_timing.get("start", 0.0) or 0.0)
    end = float(word_timing.get("end", start) or start)
    return (start + end) / 2


def live_row_bounds(live_row: dict[str, Any]) -> tuple[float, float]:
    """Return start/end seconds for one live transcript row.

    Args:
        live_row: Stored live row; missing timing means the row cannot contain a word.

    Returns:
        Start and end seconds, clamped so end is never before start.
    """
    start = float(live_row.get("start", 0.0) or 0.0)
    end = float(live_row.get("end", start) or start)
    # Bad live timing should not make nearest-row assignment impossible.
    if end < start:
        end = start

    return start, end


def assign_word_to_live_row(
    word_timing: dict[str, Any],
    live_rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Choose the live row that best owns one corrected ASR word.

    Args:
        word_timing: Timed ASR word; missing times can only use nearest-row fallback.
        live_rows: Visible transcript rows from the same replay; empty leaves the word unassigned.

    Returns:
        Assignment details, or None when no live row exists.
    """
    # No live rows means no speaker/role scaffold exists for this word.
    if live_rows == []:
        return None

    center = word_center_seconds(word_timing)
    nearest_assignment: dict[str, Any] | None = None
    nearest_gap = float("inf")

    # Each live row is checked for direct containment before using nearest fallback.
    for row_index, live_row in enumerate(live_rows, start=1):
        row_start, row_end = live_row_bounds(live_row)
        # A contained word is the cleanest alignment evidence for the user-visible row.
        if row_start <= center <= row_end:
            return {
                "match": "contains",
                "row_index": row_index,
                "segment_id": live_row.get("segment_id"),
                "role": live_row.get("role"),
                "speaker_id": live_row.get("speaker_id"),
                "row_start": row_start,
                "row_end": row_end,
                "row_text": live_row.get("text"),
                "gap_seconds": 0.0,
            }

        gap = min(abs(center - row_start), abs(center - row_end))
        # The nearest row is diagnostic when estimates drift across row boundaries.
        if gap < nearest_gap:
            nearest_gap = gap
            nearest_assignment = {
                "match": "nearest",
                "row_index": row_index,
                "segment_id": live_row.get("segment_id"),
                "role": live_row.get("role"),
                "speaker_id": live_row.get("speaker_id"),
                "row_start": row_start,
                "row_end": row_end,
                "row_text": live_row.get("text"),
                "gap_seconds": round(gap, 3),
            }

    return nearest_assignment


def assign_words_to_live_rows(
    word_timings: list[dict[str, Any]],
    live_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach live-row assignment metadata to every timed ASR word.

    Args:
        word_timings: Timed ASR words from the post-visit probe; empty means no report rows.
        live_rows: Live transcript rows from the replay; empty leaves every word unassigned.

    Returns:
        Word rows with assignment details for QA inspection.
    """
    assigned_words: list[dict[str, Any]] = []
    # Each corrected word gets the row a clinician would have seen at that time.
    for word_timing in word_timings:
        assigned_word = dict(word_timing)
        assigned_word["live_row"] = assign_word_to_live_row(word_timing, live_rows)
        assigned_words.append(assigned_word)

    return assigned_words


def find_phrase_occurrences(
    assigned_words: list[dict[str, Any]],
    phrase: str,
) -> list[dict[str, Any]]:
    """Find target phrase occurrences in timed ASR words.

    Args:
        assigned_words: Word rows with live-row assignments; empty means no phrase matches.
        phrase: Phrase to locate; empty means no phrase matches.

    Returns:
        Phrase occurrences with timing and live-row ownership evidence.
    """
    phrase_words = normalized_phrase_words(phrase)
    # Empty search text should not produce accidental matches.
    if phrase_words == []:
        return []

    normalized_words = [normalized_word(str(word.get("word", ""))) for word in assigned_words]
    occurrences: list[dict[str, Any]] = []
    # Each word position can start a phrase occurrence.
    for start_index in range(len(normalized_words) - len(phrase_words) + 1):
        # Only exact normalized phrase matches are reported.
        if normalized_words[start_index : start_index + len(phrase_words)] != phrase_words:
            continue

        phrase_slice = assigned_words[start_index : start_index + len(phrase_words)]
        live_rows = []
        # A phrase may cross rows; keep the unique rows in spoken order.
        for word in phrase_slice:
            live_row = word.get("live_row")
            # Unassigned words have no row to include.
            if live_row is None:
                continue

            row_key = (live_row.get("row_index"), live_row.get("match"))
            # Repeated row assignments would clutter the phrase evidence.
            if any(existing.get("row_key") == row_key for existing in live_rows):
                continue

            row_copy = dict(live_row)
            row_copy["row_key"] = row_key
            live_rows.append(row_copy)

        occurrences.append(
            {
                "start_word_index": phrase_slice[0].get("index"),
                "end_word_index": phrase_slice[-1].get("index"),
                "text": " ".join(str(word.get("word", "")) for word in phrase_slice),
                "start": phrase_slice[0].get("start"),
                "end": phrase_slice[-1].get("end"),
                "live_rows": live_rows,
            }
        )

    return occurrences


def build_alignment_report(
    timestamp_report: dict[str, Any],
    live_history: dict[str, Any],
    phrase: str,
) -> dict[str, Any]:
    """Build the word-to-live-row alignment report.

    Args:
        timestamp_report: Probe JSON with word timings; empty timings mean alignment is unavailable.
        live_history: Live history JSON from the same replay or fixture; empty rows leave words unassigned.
        phrase: Phrase to highlight in the report; empty skips phrase evidence.

    Returns:
        QA report with word assignments and phrase occurrence evidence.
    """
    word_timings = list(timestamp_report.get("word_timings", []))
    live_rows = list(live_history.get("segments", []))
    assigned_words = assign_words_to_live_rows(word_timings, live_rows)
    phrase_occurrences = find_phrase_occurrences(assigned_words, phrase)
    contained_word_count = sum(
        1
        for word in assigned_words
        if (word.get("live_row") or {}).get("match") == "contains"
    )

    return {
        "source": "post_visit_word_alignment_report",
        "model": timestamp_report.get("model"),
        "audio": timestamp_report.get("audio"),
        "phrase": phrase,
        "word_count": len(assigned_words),
        "live_row_count": len(live_rows),
        "contained_word_count": contained_word_count,
        "nearest_word_count": len(assigned_words) - contained_word_count,
        "phrase_occurrences": phrase_occurrences,
        "assigned_words": assigned_words,
    }


def parse_args() -> argparse.Namespace:
    """Parse local report inputs for one fixture replay.

    Returns:
        Parsed CLI options; missing required artifact paths exit through argparse.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timestamp-report", type=Path, required=True)
    parser.add_argument("--live-history", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--phrase", default=DEFAULT_PHRASE)
    return parser.parse_args()


def main() -> int:
    """Write a word-to-live-row alignment report.

    Returns:
        Process exit code; zero means the alignment report was written.
    """
    args = parse_args()
    report = build_alignment_report(
        load_json(args.timestamp_report),
        load_json(args.live_history),
        args.phrase,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        "post-visit-word-alignment "
        f"words={report['word_count']} "
        f"phrase_occurrences={len(report['phrase_occurrences'])} "
        f"output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
