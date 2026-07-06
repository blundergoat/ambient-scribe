#!/usr/bin/env python3
"""Run one offline ASR second pass and emit scoreable history JSON.

This is a fixture-spike helper, not a live server path. It loads a candidate
ASR model only when invoked directly, transcribes one WAV, and writes a
`/session/{id}/history`-shaped JSON artifact so existing quality tooling can
compare text quality before we add diarization alignment.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "nvidia/parakeet-unified-en-0.6b"
DEFAULT_MAX_WORDS_PER_SEGMENT = 18


@dataclass(frozen=True)
class AudioInfo:
    """Basic WAV metadata needed for scoreable transcript timing."""

    duration_seconds: float
    sample_rate: int
    channels: int
    sample_width: int


def wav_info(path: Path) -> AudioInfo:
    """Read duration and format from a WAV file.

    Args:
        path: Input WAV path. Missing or non-WAV files raise clear exceptions.

    Returns:
        Audio metadata used to distribute text over the fixture duration.
    """
    with wave.open(str(path), "rb") as wav_file:
        sample_rate = wav_file.getframerate()
        frames = wav_file.getnframes()
        return AudioInfo(
            duration_seconds=frames / sample_rate if sample_rate else 0.0,
            sample_rate=sample_rate,
            channels=wav_file.getnchannels(),
            sample_width=wav_file.getsampwidth(),
        )


def clipped_wav_copy(path: Path, seconds: float | None) -> tuple[Path, AudioInfo]:
    """Return a WAV path limited to `seconds` without mutating the source file."""
    source_info = wav_info(path)
    if seconds is None:
        return path, source_info
    if seconds <= 0:
        raise ValueError("--seconds must be positive")
    if seconds >= source_info.duration_seconds:
        return path, source_info

    with wave.open(str(path), "rb") as source:
        frame_limit = min(source.getnframes(), int(seconds * source.getframerate()))
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
            clipped_path = Path(temp_file.name)
        with wave.open(str(clipped_path), "wb") as clipped:
            clipped.setnchannels(source.getnchannels())
            clipped.setsampwidth(source.getsampwidth())
            clipped.setframerate(source.getframerate())
            clipped.writeframes(source.readframes(frame_limit))

    return clipped_path, wav_info(clipped_path)


def normalise_text(value: Any) -> str:
    """Extract plain transcript text from a NeMo transcription result."""
    if value is None:
        return ""
    text = getattr(value, "text", None)
    if text is not None:
        return str(text).strip()
    if isinstance(value, dict) and "text" in value:
        return str(value["text"]).strip()
    return str(value).strip()


def transcribe_audio(model_name: str, audio_path: Path) -> str:
    """Run NeMo ASRModel transcription for one audio file.

    Args:
        model_name: Hugging Face/NVIDIA model id accepted by NeMo.
        audio_path: WAV path visible inside the current process.

    Returns:
        Transcript text for the first audio item.
    """
    try:
        import nemo.collections.asr as nemo_asr
    except Exception as exc:  # pragma: no cover - depends on GPU container.
        raise RuntimeError(
            "NeMo ASR is unavailable in this Python environment. "
            "Run through scripts/eval-second-pass.sh so inference executes "
            "inside the nemo-agent container."
        ) from exc

    try:
        asr_model = nemo_asr.models.ASRModel.from_pretrained(model_name=model_name)
        result = asr_model.transcribe([str(audio_path)])
    except Exception as exc:  # pragma: no cover - depends on model/runtime.
        raise RuntimeError(f"second-pass ASR failed for model {model_name}: {exc}") from exc

    if not result:
        return ""
    return normalise_text(result[0])


def split_words(text: str) -> list[str]:
    """Return display words while preserving punctuation attached to words."""
    return re.findall(r"\S+", text.strip())


def chunk_transcript_words(words: list[str], max_words: int) -> list[list[str]]:
    """Split words into readable pseudo-segments for scoring.

    Args:
        words: Transcript tokens in ASR order.
        max_words: Hard cap per output row; small rows make review and scoring easier.

    Returns:
        Word chunks. Empty input produces an empty list.
    """
    if not words:
        return []

    chunks: list[list[str]] = []
    current: list[str] = []
    sentence_end = re.compile(r"[.!?]$")

    for word in words:
        current.append(word)
        should_close = len(current) >= max_words or bool(sentence_end.search(word))
        if should_close:
            chunks.append(current)
            current = []

    if current:
        chunks.append(current)

    return chunks


def build_history(
    *,
    transcript: str,
    audio: AudioInfo,
    model_name: str,
    source_audio: Path,
    max_words_per_segment: int,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Build a history-shaped artifact for transcript-quality scoring."""
    words = split_words(transcript)
    chunks = chunk_transcript_words(words, max_words_per_segment)
    duration = max(audio.duration_seconds, 0.0)
    total_words = max(1, len(words))
    elapsed_words = 0
    segments: list[dict[str, Any]] = []

    for index, chunk in enumerate(chunks):
        start_word = elapsed_words
        elapsed_words += len(chunk)
        start = duration * (start_word / total_words)
        end = duration * (elapsed_words / total_words)
        segments.append(
            {
                "segment_id": f"second_pass_{index:04d}",
                "speaker_id": "UNKNOWN",
                "role": "UNKNOWN",
                "start": round(start, 3),
                "end": round(max(end, start), 3),
                "text": " ".join(chunk),
                "source": "second_pass_asr",
                "model": model_name,
            }
        )

    return {
        "session_id": "second-pass-fixture",
        "source": "second_pass_asr",
        "model": model_name,
        "source_audio": str(source_audio),
        "dry_run": dry_run,
        "audio": {
            "duration_seconds": audio.duration_seconds,
            "sample_rate": audio.sample_rate,
            "channels": audio.channels,
            "sample_width": audio.sample_width,
        },
        "segments": segments,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON with stable key order for easy diffing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, required=True, help="Input WAV path")
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"NeMo ASR model id (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--history-output",
        type=Path,
        required=True,
        help="Destination history-shaped JSON",
    )
    parser.add_argument(
        "--metadata-output",
        type=Path,
        default=None,
        help="Optional metadata JSON destination",
    )
    parser.add_argument(
        "--max-words-per-segment",
        type=int,
        default=DEFAULT_MAX_WORDS_PER_SEGMENT,
        help=f"Pseudo-segment word cap (default: {DEFAULT_MAX_WORDS_PER_SEGMENT})",
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=None,
        help="Optional leading audio duration to transcribe for fixture smoke runs",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate audio and write an empty artifact without loading NeMo",
    )
    return parser.parse_args()


def main() -> int:
    """Run the second-pass ASR helper."""
    args = parse_args()
    audio_path = args.audio
    if not audio_path.exists():
        print(f"error: audio file not found: {audio_path}", file=sys.stderr)
        return 2
    if args.max_words_per_segment <= 0:
        print("error: --max-words-per-segment must be positive", file=sys.stderr)
        return 2

    transcribe_path: Path | None = None
    try:
        transcribe_path, audio = clipped_wav_copy(audio_path, args.seconds)
        transcript = "" if args.dry_run else transcribe_audio(args.model, transcribe_path)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        if transcribe_path is not None and transcribe_path != audio_path:
            transcribe_path.unlink(missing_ok=True)

    history = build_history(
        transcript=transcript,
        audio=audio,
        model_name=args.model,
        source_audio=audio_path,
        max_words_per_segment=args.max_words_per_segment,
        dry_run=args.dry_run,
    )
    write_json(args.history_output, history)

    metadata = {
        "model": args.model,
        "audio": history["audio"],
        "history_output": str(args.history_output),
        "segment_count": len(history["segments"]),
        "word_count": len(split_words(transcript)),
        "dry_run": args.dry_run,
        "seconds": args.seconds,
    }
    if args.metadata_output is not None:
        write_json(args.metadata_output, metadata)

    print(
        "second-pass-asr "
        f"model={args.model} "
        f"segments={metadata['segment_count']} "
        f"words={metadata['word_count']} "
        f"history={args.history_output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
