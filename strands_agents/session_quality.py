"""
Session quality records for completed browser recordings.

The streaming route uses this module after a consultation stops to turn in-memory
audio, role, and latency counters into one compact JSON-safe record. Operators,
the dev State tab, and fixture evals use that record to answer whether a visible
session was healthy without reading transcript text.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from math import ceil, floor
from pathlib import Path
from typing import Any, Iterable


QUALITY_RECORD_SCHEMA_VERSION = 1
QUALITY_RECORD_DEFAULT_DIR = Path("var/quality")
QUALITY_RECORD_FILENAME = "sessions.jsonl"


@dataclass(slots=True)
class TranscriptionQualityStats:
    """
    In-memory counters for one visible recording.

    `TranscriptionSession` updates these counters while audio windows run, but
    no file I/O happens until finalize. Empty samples mean the user stopped
    before the metric had a chance to exist.

    Attributes:
        window_seconds: Size of each NeMo window sent for transcription.
        emitted_segment_count: Transcript lines shown to the user.
        held_segment_count: Tail lines delayed so the final text can stabilize.
        phantom_speaker_merge_count: Speaker merges; currently zero until M16 adds detection.
        speaker_anchor_remap_count: Window speaker IDs remapped to preserve visible identity.
    """

    window_seconds: list[float] = field(default_factory=list)
    emitted_segment_count: int = 0
    held_segment_count: int = 0
    phantom_speaker_merge_count: int = 0
    speaker_anchor_remap_count: int = 0

    def record_window(self, window_audio_bytes: int) -> None:
        """Add one NeMo window size for the final quality summary.

        Args:
            window_audio_bytes: PCM byte length; zero means no audio reached NeMo for this pass.
        """
        # Empty windows are skipped before NeMo runs, so they should not move p50/p95.
        if window_audio_bytes <= 0:
            return

        self.window_seconds.append(round(window_audio_bytes / 32000, 3))

    def record_segment_flow(self, emitted_segments: int, held_segments: int) -> None:
        """Count text that became visible and text delayed for stability.

        Args:
            emitted_segments: Lines published to the browser; zero means no new visible text.
            held_segments: Tail lines withheld until more audio or finalize.
        """
        self.emitted_segment_count += emitted_segments
        self.held_segment_count += held_segments

    def record_speaker_anchor_remaps(self, remapped_speakers: int) -> None:
        """Count speaker IDs remapped between windows for stable labels.

        Args:
            remapped_speakers: Speaker IDs corrected in one window; zero means no visible identity shift.
        """
        # A remap only matters when a browser-visible speaker identity was corrected.
        if remapped_speakers <= 0:
            return

        self.speaker_anchor_remap_count += remapped_speakers

    def record_phantom_speaker_merges(self, merged_speakers: int) -> None:
        """Count extra speaker IDs merged back into visible consultation speakers.

        Args:
            merged_speakers: Extra IDs collapsed in one window; zero means no phantom reached the cap.
        """
        # A zero merge count means the user-visible speaker set did not need correction.
        if merged_speakers <= 0:
            return

        self.phantom_speaker_merge_count += merged_speakers


def build_session_quality_record(
    *,
    session_id: str,
    audio_session: Any,
    stream_state: Any,
    role_state: Any,
    status: str = "finalized",
    finalized_at: datetime | None = None,
) -> dict[str, Any]:
    """Build the single quality record for a completed recording.

    Args:
        session_id: Browser session UUID; empty would make the record impossible to join.
        audio_session: TranscriptionSession carrying audio/window counters.
        stream_state: WebSocket loop state with chunk latency and error counts.
        role_state: Role mapping state; empty history means labels never stabilized.
        status: Terminal state shown in logs; empty is treated as `finalized`.
        finalized_at: Override timestamp for tests; null uses the current UTC time.

    Returns:
        JSON-safe metrics record without transcript text.
    """
    finished_at = finalized_at or datetime.now(UTC)
    quality_stats = audio_session.quality_stats
    chunk_inference_ms = getattr(stream_state, "chunk_inference_ms", [])
    chunk_total_ms = getattr(stream_state, "chunk_total_ms", [])
    role_mapping_history = getattr(role_state, "mapping_history", [])
    final_confidence = getattr(role_state, "running_confidence", 0.0)
    role_truncation_events = getattr(role_state, "truncation_events", 0)

    return {
        "schema_version": QUALITY_RECORD_SCHEMA_VERSION,
        "type": "quality",
        "session_id": session_id,
        "status": status or "finalized",
        "finalized_at": finished_at.isoformat().replace("+00:00", "Z"),
        "chunks": getattr(stream_state, "chunk_count", 0),
        "audio_seconds": round(audio_session.buffer.duration_seconds, 3),
        "session_duration_seconds": round(
            finished_at.timestamp() - audio_session.started_at, 3
        ),
        "stored_segments": len(audio_session.accumulated_transcript),
        "emitted_segments": quality_stats.emitted_segment_count,
        "held_segments": quality_stats.held_segment_count,
        "window_seconds": _summarize_numeric_samples(quality_stats.window_seconds),
        "chunk_inference_ms": _summarize_numeric_samples(chunk_inference_ms),
        "chunk_total_ms": _summarize_numeric_samples(chunk_total_ms),
        "phantom_speaker_merges": quality_stats.phantom_speaker_merge_count,
        "speaker_anchor_remaps": quality_stats.speaker_anchor_remap_count,
        "role_flips_accepted": _count_role_label_flips(role_mapping_history),
        "role_flips_suppressed": getattr(role_state, "suppressed_flip_count", 0),
        "role_truncation_events": int(role_truncation_events),
        "final_confidence": round(float(final_confidence), 3),
        "error_count": getattr(stream_state, "error_count", 0),
    }


def persist_session_quality_record(
    quality_record: dict[str, Any],
    *,
    base_directory: Path | str | None = None,
) -> Path:
    """Append a completed quality record to the local JSONL trend source.

    Args:
        quality_record: Record returned by `build_session_quality_record`; empty means no row is written.
        base_directory: Output directory; null uses `SESSION_QUALITY_DIR` or `var/quality`.

    Returns:
        Path to the JSONL file; parent directories are created on demand.
    """
    record_path = _quality_record_file_path(base_directory)
    record_path.parent.mkdir(parents=True, exist_ok=True)

    # Empty records mean there is no trustworthy quality row for the developer to inspect.
    if not quality_record:
        return record_path

    with record_path.open("a", encoding="utf-8") as record_file:
        json.dump(quality_record, record_file, sort_keys=True)
        record_file.write("\n")

    return record_path


def _quality_record_file_path(base_directory: Path | str | None) -> Path:
    """Resolve the JSONL path for local quality records.

    Args:
        base_directory: Explicit directory from tests or scripts; null uses environment/default.

    Returns:
        Path ending in `sessions.jsonl` under a gitignored directory.
    """
    resolved_directory = base_directory

    # Null directory means callers want the operator-configured default path.
    if resolved_directory is None:
        resolved_directory = os.environ.get("SESSION_QUALITY_DIR", "")

    # Empty environment values fall back to the repo-local var directory.
    if resolved_directory in (None, ""):
        resolved_directory = QUALITY_RECORD_DEFAULT_DIR

    return Path(resolved_directory) / QUALITY_RECORD_FILENAME


def _summarize_numeric_samples(values: Iterable[float | int]) -> dict[str, float | int]:
    """Summarize metric samples for a compact dev-panel display.

    Args:
        values: Numeric samples; empty means the session did not exercise that path.

    Returns:
        Count, p50, p95, and max values rounded for logs and JSONL.
    """
    numeric_values = [float(value) for value in values]

    # Empty samples keep downstream report tables stable without inventing latency.
    if numeric_values == []:
        return {"count": 0, "p50": 0.0, "p95": 0.0, "max": 0.0}

    return {
        "count": len(numeric_values),
        "p50": round(_percentile(numeric_values, 50), 3),
        "p95": round(_percentile(numeric_values, 95), 3),
        "max": round(max(numeric_values), 3),
    }


def _percentile(values: list[float], percentile: float) -> float:
    """Return an interpolated percentile for a metric sample set.

    Args:
        values: Non-empty numeric samples.
        percentile: Percentile from 0 to 100; out-of-range values clamp to the ends.

    Returns:
        Interpolated value; single-value input returns that value.
    """
    ordered_values = sorted(values)

    # A single sample is both p50 and p95 from the user's point of view.
    if len(ordered_values) == 1:
        return ordered_values[0]

    clamped_percentile = max(0.0, min(100.0, percentile))
    rank = (len(ordered_values) - 1) * (clamped_percentile / 100)
    lower_index = floor(rank)
    upper_index = ceil(rank)

    # Exact ranks avoid tiny floating-point interpolation noise in reports.
    if lower_index == upper_index:
        return ordered_values[lower_index]

    lower_value = ordered_values[lower_index]
    upper_value = ordered_values[upper_index]
    return lower_value + (upper_value - lower_value) * (rank - lower_index)


def _count_role_label_flips(mapping_history: list[dict[str, str]]) -> int:
    """Count accepted role-label flips from role mapping history.

    Args:
        mapping_history: Consecutive speaker-role snapshots; empty means no role events reached the UI.

    Returns:
        Number of times at least two existing speakers swapped labels.
    """
    flip_count = 0

    # Adjacent snapshots show how labels changed during the visible session.
    for previous_mapping, current_mapping in zip(
        mapping_history, mapping_history[1:], strict=False
    ):
        common_speakers = previous_mapping.keys() & current_mapping.keys()
        changed_speakers = [
            speaker_id
            for speaker_id in common_speakers
            if previous_mapping[speaker_id] != current_mapping[speaker_id]
        ]

        # Single-speaker changes are corrections/new evidence, not a visible role flip.
        if len(changed_speakers) < 2:
            continue

        previous_roles = {previous_mapping[speaker_id] for speaker_id in changed_speakers}
        current_roles = {current_mapping[speaker_id] for speaker_id in changed_speakers}

        # A flip means the same role set moved to different speakers.
        if previous_roles == current_roles:
            flip_count += 1

    return flip_count
