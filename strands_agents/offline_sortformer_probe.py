#!/usr/bin/env python3
"""Probe full-audio Sortformer diarization for offline QA.

Use this inside the `nemo-agent` container after a stopped consultation needs
speaker-boundary evidence. It loads Sortformer in a separate process, diarizes a
fixture clip, and writes raw speaker-time segments for host-side scoring.
The output is QA evidence only; it does not mutate FastAPI, storage, browser
events, the live NeMo singleton, or corrected transcript storage.
"""

from __future__ import annotations

import argparse
import importlib.metadata as package_metadata
import json
import tempfile
import time
import wave
from pathlib import Path
from typing import Any

DEFAULT_SORTFORMER_MODEL = "nvidia/diar_streaming_sortformer_4spk-v2.1"


def clipped_wav_for_diarization(source_audio_path: Path, seconds_limit: float | None) -> Path:
    """Create the audio clip Sortformer should diarize for this fixture score.

    Args:
        source_audio_path: WAV selected by the developer; missing files fail before model load.
        seconds_limit: Leading seconds to diarize; null means use the whole stopped recording.

    Returns:
        Temporary WAV path; caller deletes it after the probe finishes.

    Raises:
        FileNotFoundError: When the selected fixture audio is unavailable inside the container.
        ValueError: When the requested duration is zero or negative, which cannot score a visit.
    """
    # Missing audio means the user has not supplied the fixture to the container.
    if not source_audio_path.exists():
        raise FileNotFoundError(f"audio file not found: {source_audio_path}")

    # A non-positive clip would create an empty post-stop transcript candidate.
    if seconds_limit is not None and seconds_limit <= 0:
        raise ValueError("--seconds must be positive when provided")

    with wave.open(str(source_audio_path), "rb") as source_audio:
        frame_limit = source_audio.getnframes()
        # A seconds cap lets consult-03 compare against the existing 60s baseline.
        if seconds_limit is not None:
            frame_limit = min(
                frame_limit,
                int(seconds_limit * source_audio.getframerate()),
            )

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as scratch_audio:
            clipped_audio_path = Path(scratch_audio.name)

        with wave.open(str(clipped_audio_path), "wb") as clipped_audio:
            clipped_audio.setnchannels(source_audio.getnchannels())
            clipped_audio.setsampwidth(source_audio.getsampwidth())
            clipped_audio.setframerate(source_audio.getframerate())
            clipped_audio.writeframes(source_audio.readframes(frame_limit))

    return clipped_audio_path


def wav_info(audio_path: Path) -> dict[str, Any]:
    """Return the WAV facts needed to interpret diarization timestamps.

    Args:
        audio_path: Clip passed to Sortformer; invalid WAV files fail the probe.

    Returns:
        Sample rate, channel count, frame count, and duration; zero duration means no scoreable audio.
    """
    with wave.open(str(audio_path), "rb") as audio_file:
        sample_rate = audio_file.getframerate()
        frame_count = audio_file.getnframes()
        duration_seconds = 0.0
        # Missing sample-rate metadata means the clip cannot show a trustworthy duration.
        if sample_rate:
            duration_seconds = frame_count / sample_rate

        return {
            "sample_rate": sample_rate,
            "channels": audio_file.getnchannels(),
            "frames": frame_count,
            "duration_seconds": duration_seconds,
        }


def package_version(package_name: str) -> str | None:
    """Return an installed package version for artifact provenance.

    Args:
        package_name: Python distribution name; empty names are treated as unavailable.

    Returns:
        Version string, or None when the package metadata is absent in this container.
    """
    # Empty names do not identify a package the developer can verify later.
    if package_name == "":
        return None

    try:
        return package_metadata.version(package_name)
    except package_metadata.PackageNotFoundError:
        return None


def parse_sortformer_segment(raw_segment: Any, segment_index: int) -> dict[str, Any] | None:
    """Convert one Sortformer segment string into the neutral artifact shape.

    Args:
        raw_segment: Sortformer row, normally `start end speaker_id`; empty rows are skipped.
        segment_index: One-based order in the diarization output; zero would confuse artifact IDs.

    Returns:
        Segment dictionary, or None when the model row cannot be scored.
    """
    parts = str(raw_segment).strip().split()
    # Rows without start/end/speaker cannot contribute to Doctor/Patient scoring.
    if len(parts) < 3:
        return None

    try:
        segment_start = float(parts[0])
        segment_end = float(parts[1])
    except ValueError:
        return None

    # Backwards rows would invert the transcript span shown to the scorer.
    if segment_end < segment_start:
        segment_end = segment_start

    return {
        "segment_id": f"diar-{segment_index:04d}",
        "start": round(segment_start, 3),
        "end": round(segment_end, 3),
        "speaker_id": parts[2],
        "confidence": None,
        "source": "sortformer_raw",
        "raw": str(raw_segment),
    }


def normalize_diarization_output(diarization_output: Any) -> list[dict[str, Any]]:
    """Normalize Sortformer output into speaker-time rows.

    Args:
        diarization_output: Raw `diarize(...)` return value; empty means no speaker turns.

    Returns:
        Neutral diarization segments; empty means no offline speaker scaffold exists.
    """
    # Empty model output means the candidate cannot improve the post-stop transcript.
    if not diarization_output:
        return []

    first_file_segments: list[Any] = []
    # Sortformer returns one segment list per audio file; this probe scores one stopped visit.
    if isinstance(diarization_output, list):
        first_file_segments = diarization_output[0]

    normalized_segments: list[dict[str, Any]] = []
    # Each Sortformer row describes one speaker-active time span.
    for segment_index, raw_segment in enumerate(first_file_segments, start=1):
        normalized_segment = parse_sortformer_segment(raw_segment, segment_index)
        # Malformed rows are omitted rather than producing misleading Unknown transcript rows.
        if normalized_segment is None:
            continue

        normalized_segments.append(normalized_segment)

    return normalized_segments


def raw_output_rows_from_sortformer(diarization_output: Any) -> list[str]:
    """Return vendor rows as strings for QA artifact inspection.

    Args:
        diarization_output: Raw Sortformer output; empty means no turn evidence was produced.

    Returns:
        Raw rows for the first audio file; empty means the scorer has no diarization evidence.
    """
    # Empty model output means the artifact should show no vendor rows.
    if not diarization_output:
        return []

    first_file_segments: list[Any] = []
    # One fixture audio is scored at a time, so only the first model output list is relevant.
    if isinstance(diarization_output, list):
        first_file_segments = diarization_output[0]

    raw_output_rows: list[str] = []
    # Preserve every vendor row so a developer can compare raw and normalized segments.
    for raw_segment in first_file_segments:
        raw_output_rows.append(str(raw_segment))

    return raw_output_rows


def run_sortformer_probe(
    audio_path: Path,
    output_path: Path,
    model_name: str,
    seconds_limit: float | None,
) -> dict[str, Any]:
    """Run Sortformer full-audio diarization and write a neutral JSON artifact.

    Args:
        audio_path: WAV path available inside the container; missing files fail before model load.
        output_path: JSON artifact path; parent directories are created for the developer.
        model_name: Sortformer checkpoint to load; empty would make provenance unusable.
        seconds_limit: Leading seconds to score; null means the whole recording after Stop.

    Returns:
        Artifact dictionary written to disk for host-side scoring.
    """
    import nemo
    import torch
    from nemo.collections.asr.models import SortformerEncLabelModel

    clipped_audio_path = clipped_wav_for_diarization(audio_path, seconds_limit)
    try:
        clip_info = wav_info(clipped_audio_path)
        device_name = "cpu"
        # GPU availability decides whether this probe exercises the same path as live transcription.
        if torch.cuda.is_available():
            device_name = "cuda"

        device = torch.device(device_name)
        started_at = time.monotonic()
        diar_model = (
            SortformerEncLabelModel.from_pretrained(model_name)
            .eval()
            .to(device)
        )

        with torch.inference_mode():
            diarization_output = diar_model.diarize(
                audio=str(clipped_audio_path),
                batch_size=1,
                verbose=False,
            )

        elapsed_seconds = time.monotonic() - started_at
    finally:
        clipped_audio_path.unlink(missing_ok=True)

    artifact = {
        "source": "offline_diarization_candidate",
        "schema_version": 1,
        "candidate": {
            "id": "sortformer-v2.1-full-audio",
            "model": model_name,
            "engine": "nemo",
            "runtime": "nemo-agent",
            "versions": {
                "nemo": getattr(nemo, "__version__", None),
                "nemo_toolkit_metadata": package_version("nemo_toolkit"),
                "torch": getattr(torch, "__version__", None),
            },
        },
        "audio": {
            "path": str(audio_path),
            "cutoff_seconds": seconds_limit,
            "sample_rate": clip_info["sample_rate"],
            "channels": clip_info["channels"],
            "frames": clip_info["frames"],
            "duration_seconds": round(clip_info["duration_seconds"], 3),
        },
        "runtime_seconds": round(elapsed_seconds, 3),
        "raw_output": raw_output_rows_from_sortformer(diarization_output),
        "diarization_segments": normalize_diarization_output(diarization_output),
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(artifact, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return artifact


def parse_args() -> argparse.Namespace:
    """Parse local Sortformer probe options.

    Returns:
        Parsed CLI options; missing audio/output paths exit through argparse.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default=DEFAULT_SORTFORMER_MODEL)
    parser.add_argument("--seconds", type=float, default=None)
    return parser.parse_args()


def main() -> int:
    """Write one offline Sortformer diarization artifact.

    Returns:
        Process exit code; zero means the raw diarization artifact was written.
    """
    args = parse_args()
    artifact = run_sortformer_probe(
        audio_path=args.audio,
        output_path=args.output,
        model_name=args.model,
        seconds_limit=args.seconds,
    )
    print(
        "offline-sortformer-probe "
        f"segments={len(artifact['diarization_segments'])} "
        f"seconds={artifact['audio']['duration_seconds']} "
        f"runtime={artifact['runtime_seconds']} "
        f"output={args.output}"
    )
    return 0


# Command-line use starts the fixture-only diarization probe.
if __name__ == "__main__":
    raise SystemExit(main())
