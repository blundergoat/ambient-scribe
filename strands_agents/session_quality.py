"""
Session quality records for completed browser recordings.

The streaming route uses this module once a consultation stops, to turn in-memory audio, role, and latency counters into
one compact JSON-safe record.

Operators, the dev State tab, and fixture evaluations all read that record to answer a single question: was this visit
healthy? Because it carries counts and timings only, that question can be answered without anyone reading transcript text.
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

    `TranscriptionSession` updates these while audio windows run, and nothing is written to disk until the visit finalizes.

    Empty samples mean the clinician stopped before that metric had a chance to exist, which is a normal short-visit
    outcome rather than a missing measurement.

    Attributes:
        window_seconds: Size of each NeMo window sent for transcription; empty when no window completed.
        emitted_segment_count: Transcript lines shown to the user.
        held_segment_count: Tail lines delayed so the final text can stabilize.
        phantom_speaker_merge_count: Extra diarization speakers folded back into a real one; zero means the speaker set
            needed no correction during this visit.
        speaker_anchor_remap_count: Window speaker IDs remapped to preserve visible identity.
    """

    window_seconds: list[float] = field(default_factory=list)
    emitted_segment_count: int = 0
    held_segment_count: int = 0
    phantom_speaker_merge_count: int = 0
    speaker_anchor_remap_count: int = 0

    def record_window(self, window_audio_bytes: int) -> None:
        """Add one NeMo window size for the final quality summary.

        Called per transcription pass, so the finished record shows how much audio each pass actually carried.

        Args:
            window_audio_bytes: PCM byte length; zero means no audio reached NeMo for this pass.
        """
        # Empty windows are skipped before NeMo runs, so counting them would drag the p50 and p95 toward zero.
        if window_audio_bytes <= 0:
            return

        self.window_seconds.append(round(window_audio_bytes / 32000, 3))

    def record_segment_flow(self, emitted_segments: int, held_segments: int) -> None:
        """Count text that became visible and text delayed for stability.

        The gap between the two is what explains a clinician's impression that the transcript lags behind the conversation.

        Args:
            emitted_segments: Lines published to the browser; zero means no new visible text.
            held_segments: Tail lines withheld until more audio arrives or the visit finalizes.
        """
        self.emitted_segment_count += emitted_segments
        self.held_segment_count += held_segments

    def record_speaker_anchor_remaps(self, remapped_speakers: int) -> None:
        """Count speaker IDs remapped between windows for stable labels.

        Diarization can rename the same person between windows, and a remap is the correction that stops the browser card
        from flipping between Doctor and Patient mid-sentence.

        Args:
            remapped_speakers: Speaker IDs corrected in one window; zero means no visible identity shift.
        """
        # Only a real correction is counted, so a quiet window does not inflate the visit's remap total.
        if remapped_speakers <= 0:
            return

        self.speaker_anchor_remap_count += remapped_speakers

    def record_phantom_speaker_merges(self, merged_speakers: int) -> None:
        """Count extra speaker IDs merged back into visible consultation speakers.

        A two-person consultation that diarizes into three or more speakers would show the clinician a third participant
        who was never in the room, so the extras are folded back into a real speaker and counted here.

        Args:
            merged_speakers: Extra IDs collapsed in one window; zero means no phantom reached the cap.
        """
        # Only a real merge is counted, so an ordinary two-speaker window leaves the visit's merge total alone.
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

    Called once, as the visit finalizes, so everything the clinician experienced during that consultation collapses into
    one row an operator can scan later.

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
        "engine": getattr(audio_session, "engine_name", "windowed"),
        "status": status or "finalized",
        "finalized_at": finished_at.isoformat().replace("+00:00", "Z"),
        # The session-wide counter survives reconnects, so a clinician who refreshed mid-visit still gets one honest total.
        # The per-socket stream state only saw chunks since the last resume and would report a fraction of the visit.
        "chunks": getattr(audio_session, "chunk_count", 0),
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


def build_quality_tail_record(
    session_id: str,
    role_state: Any,
    *,
    recorded_at: datetime | None = None,
) -> dict[str, Any] | None:
    """Build the additive post-finalize role-churn record, if any churn happened.

    The `session.quality` record closes before the role queue drains its tail, so a role label that settles after the
    clinician has already stopped is invisible to it.

    This record carries the delta between the counters at quality time, snapshotted on the role state, and the counters
    once the role worker finished. It exists so late label churn shows up somewhere rather than nowhere.

    Args:
        session_id: Browser session UUID the tail belongs to.
        role_state: Role mapping state after the queue drained.
        recorded_at: Override timestamp for tests; null uses current UTC time.

    Returns:
        JSON-safe `quality_tail` record, or None when the session never finalized a quality record or no tail churn
        occurred. A null is the ordinary outcome and means no extra row is worth writing.
    """
    snapshot = getattr(role_state, "quality_flip_snapshot", None)
    # No snapshot means the session never emitted a quality record, which happens on error paths.
    # Without that baseline there is nothing to measure a tail against, so no record is produced.
    if not snapshot:
        return None

    accepted_now = _count_role_label_flips(getattr(role_state, "mapping_history", []))
    suppressed_now = int(getattr(role_state, "suppressed_flip_count", 0))
    tail_accepted = accepted_now - int(snapshot.get("role_flips_accepted", 0))
    tail_suppressed = suppressed_now - int(snapshot.get("role_flips_suppressed", 0))

    # A quiet tail is the normal case: the labels the clinician last saw were already final, so no extra row is written.
    if tail_accepted <= 0 and tail_suppressed <= 0:
        return None

    finished_at = recorded_at or datetime.now(UTC)
    return {
        "schema_version": QUALITY_RECORD_SCHEMA_VERSION,
        "type": "quality_tail",
        "session_id": session_id,
        "recorded_at": finished_at.isoformat().replace("+00:00", "Z"),
        "tail_role_flips_accepted": max(0, tail_accepted),
        "tail_role_flips_suppressed": max(0, tail_suppressed),
        "final_role_flips_accepted": accepted_now,
        "final_role_flips_suppressed": suppressed_now,
    }


def persist_session_quality_record(
    quality_record: dict[str, Any],
    *,
    base_directory: Path | str | None = None,
) -> Path:
    """Append a completed quality record to the local JSONL trend source.

    One line per visit, so a run of consultations can be compared without opening any of them individually.

    Args:
        quality_record: Record returned by `build_session_quality_record`; empty means no row is written.
        base_directory: Output directory; null uses `SESSION_QUALITY_DIR` or `var/quality`.

    Returns:
        Path to the JSONL file; parent directories are created on demand. The path is returned even when nothing was
        appended, so callers can log where the record would have gone.
    """
    record_path = _quality_record_file_path(base_directory)
    record_path.parent.mkdir(parents=True, exist_ok=True)

    # An empty record carries no measurement, so writing it would add a row that tells a developer nothing.
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

    # A null directory means the caller wants whatever root the operator configured for this environment.
    if resolved_directory is None:
        resolved_directory = os.environ.get("SESSION_QUALITY_DIR", "")

    # An unset or blank environment value falls back to the repo-local var directory.
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

    # A zeroed summary keeps the report table's shape stable for a visit that never exercised this path, without
    # inventing a latency figure that was never measured. The count of zero is what tells the reader which it was.
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

    # A visit with one measured window has no distribution, so that single value is both its p50 and its p95.
    if len(ordered_values) == 1:
        return ordered_values[0]

    clamped_percentile = max(0.0, min(100.0, percentile))
    rank = (len(ordered_values) - 1) * (clamped_percentile / 100)
    lower_index = floor(rank)
    upper_index = ceil(rank)

    # A rank landing exactly on a sample needs no interpolation, which keeps tiny floating-point noise out of the report.
    if lower_index == upper_index:
        return ordered_values[lower_index]

    lower_value = ordered_values[lower_index]
    upper_value = ordered_values[upper_index]
    return lower_value + (upper_value - lower_value) * (rank - lower_index)


def _count_role_label_flips(mapping_history: list[dict[str, str]]) -> int:
    """Count accepted role-label flips from role mapping history.

    A flip is the visible failure a clinician notices: the Doctor and Patient cards swapping places partway through a visit.

    Args:
        mapping_history: Consecutive speaker-role snapshots; empty means no role events reached the UI.

    Returns:
        Number of times at least two existing speakers swapped labels; zero means labels only ever gained detail.
    """
    flip_count = 0

    # Each adjacent pair of snapshots is one change the clinician could have watched happen on screen.
    for previous_mapping, current_mapping in zip(
        mapping_history, mapping_history[1:], strict=False
    ):
        common_speakers = previous_mapping.keys() & current_mapping.keys()
        changed_speakers = [
            speaker_id
            for speaker_id in common_speakers
            if previous_mapping[speaker_id] != current_mapping[speaker_id]
        ]

        # One speaker changing label is the agent refining a single judgement on new evidence, not the cards swapping.
        if len(changed_speakers) < 2:
            continue

        previous_roles = {
            previous_mapping[speaker_id] for speaker_id in changed_speakers
        }
        current_roles = {current_mapping[speaker_id] for speaker_id in changed_speakers}

        # The same set of roles landing on different speakers is the swap itself, which is what gets counted.
        if previous_roles == current_roles:
            flip_count += 1

    return flip_count
