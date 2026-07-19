#!/usr/bin/env python3
"""Run one offline ASR second pass and emit scoreable history JSON.

Operators use this helper to compare post-visit transcript accuracy without
changing the Scribe screen or a saved consultation. Production mode delegates
to a fail-closed helper that reuses application correction assembly; legacy
mode remains available for explicit one-fixture wording experiments.
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import sys
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import second_pass_production as production
from second_pass_production import (
    DEVELOPMENT_MANIFEST_NAME,
    EXPECTED_DEVELOPMENT_STEMS,
    file_sha256,
    validate_production_corpus_selection,
)

__all__ = [
    "DEVELOPMENT_MANIFEST_NAME",
    "EXPECTED_DEVELOPMENT_STEMS",
    "file_sha256",
    "validate_production_corpus_selection",
]

DEFAULT_MODEL = "nvidia/parakeet-tdt-0.6b-v3"
DEFAULT_MAX_WORDS_PER_SEGMENT = 18
M02_UNIFIED_MODEL = "nvidia/parakeet-unified-en-0.6b"
M02_APPROVED_CORRECTION_PHRASE = "brand new sector"


@dataclass(frozen=True)
class AudioInfo:
    """Describe fixture audio shown in a legacy offline transcription report.

    Operators use this metadata to confirm the consultation and align candidate
    wording with the same timeline used by quality scoring.

    Attributes:
        duration_seconds: Playable length; zero means no audible content was available.
        sample_rate: Samples per second; zero means the WAV has no usable timeline.
        channels: Recorded channel count; zero means the WAV metadata is invalid.
        sample_width: Bytes per sample; zero means the WAV metadata is invalid.
    """

    duration_seconds: float
    sample_rate: int
    channels: int
    sample_width: int


def wav_info(path: Path) -> AudioInfo:
    """Read the selected fixture's format before an operator compares models.

    Args:
        path: Selected WAV; an absent path means there is no consultation to evaluate.

    Returns:
        Timeline metadata; zero duration means the fixture has no playable audio.

    Raises:
        wave.Error: The selected file is not a readable WAV consultation.
    """
    with wave.open(str(path), "rb") as wav_file:
        sample_rate = wav_file.getframerate()
        frames = wav_file.getnframes()
        duration_seconds = 0.0
        # A valid sample rate places candidate wording on the user's audio timeline.
        if sample_rate > 0:
            duration_seconds = frames / sample_rate
        return AudioInfo(
            duration_seconds=duration_seconds,
            sample_rate=sample_rate,
            channels=wav_file.getnchannels(),
            sample_width=wav_file.getsampwidth(),
        )


def clipped_wav_copy(path: Path, seconds: float | None) -> tuple[Path, AudioInfo]:
    """Prepare the leading consultation interval chosen for a quick comparison.

    Args:
        path: Source fixture; an absent path means no consultation can be clipped.
        seconds: Requested leading duration; None keeps the complete consultation.

    Returns:
        Usable WAV path and metadata; the original path means no clip was needed.

    Raises:
        ValueError: The operator selected a zero or negative playback duration.
        wave.Error: The selected fixture cannot be read or copied as WAV audio.
    """
    source_info = wav_info(path)
    # No cutoff means the operator wants the complete consultation unchanged.
    if seconds is None:
        return path, source_info
    # A non-positive cutoff would produce no consultation for the user to assess.
    if seconds <= 0:
        raise ValueError("--seconds must be positive")
    # A cutoff beyond the visit already includes every audible moment.
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
    """Extract candidate wording an operator will score from one NeMo result.

    Args:
        value: NeMo result; None means the model returned no consultation wording.

    Returns:
        Trimmed display wording; empty means the candidate produced no transcript text.
    """
    # No model result gives the quality report an honest empty transcript.
    if value is None:
        return ""
    text = getattr(value, "text", None)
    # NeMo hypothesis objects expose their user-visible wording through `text`.
    if text is not None:
        return str(text).strip()
    # Dictionary-shaped results keep older explicit experiments readable.
    if isinstance(value, dict) and "text" in value:
        return str(value["text"]).strip()
    return str(value).strip()


def transcribe_audio(model_name: str, audio_path: Path) -> str:
    """Transcribe one fixture after an operator starts a legacy comparison.

    Args:
        model_name: Selected NeMo model; empty means no candidate can be loaded.
        audio_path: Selected WAV; an absent path means no consultation can be transcribed.

    Returns:
        Candidate wording for the first item; empty means NeMo decoded no words.

    Raises:
        RuntimeError: NeMo is unavailable or cannot transcribe the selected fixture.
    """
    try:
        import nemo.collections.asr as nemo_asr
    # Example: the operator ran the helper on the host instead of the NeMo container.
    except Exception as exc:  # pragma: no cover - depends on GPU container.
        raise RuntimeError(
            "NeMo ASR is unavailable in this Python environment. "
            "Run through scripts/eval-second-pass.sh so inference executes "
            "inside the nemo-agent container."
        ) from exc

    try:
        asr_model = nemo_asr.models.ASRModel.from_pretrained(model_name=model_name)
        transcription_results = asr_model.transcribe([str(audio_path)])
    # Example: a candidate cannot construct in the pinned runtime after the user presses Stop.
    except Exception as exc:  # pragma: no cover - depends on model/runtime.
        raise RuntimeError(
            f"second-pass ASR failed for model {model_name}: {exc}"
        ) from exc

    # No decoded item leaves an explicit empty transcript instead of invented wording.
    if not transcription_results:
        return ""
    return normalise_text(transcription_results[0])


def transcribe_application_post_visit_audio(
    model_name: str,
    audio_path: Path,
    correction_phrase: str | None,
) -> tuple[str, dict[str, Any]]:
    """Run the exact stopped-visit decoder without assembling browser rows.

    Use for one approved A/B fixture; null phrase preserves the clinician's baseline words.
    """
    correction_module = importlib.import_module("post_visit_correction")
    correction_module.CAPTURE_POST_VISIT_DECODING_CONFIG = True
    transcription_result = correction_module.transcribe_audio_with_nemo(
        model_name,
        str(audio_path),
        correction_phrase=correction_phrase,
    )
    effective_decoding_config = transcription_result.effective_decoding_config
    # Missing config means the A/B cannot prove that phrase bias was its only variable.
    if effective_decoding_config is None:
        raise RuntimeError("effective decoder evidence is unavailable")
    return (
        normalise_text(transcription_result),
        effective_decoding_config,
    )


def split_words(text: str) -> list[str]:
    """Split candidate wording into display words used by legacy evidence.

    Args:
        text: Candidate transcript; empty means the model produced no visible wording.

    Returns:
        Ordered display words; empty means the report has no transcript rows.
    """
    return re.findall(r"\S+", text.strip())


def chunk_transcript_words(words: list[str], max_words: int) -> list[list[str]]:
    """Split legacy candidate words into readable score rows.

    Args:
        words: Candidate words in spoken order; empty means no transcript rows are shown.
        max_words: Row cap; zero is invalid and is rejected before this helper runs.

    Returns:
        Readable word groups; empty means the candidate produced no wording.
    """
    # No decoded words produce the report's explicit empty transcript state.
    if not words:
        return []

    display_word_chunks: list[list[str]] = []
    current_display_words: list[str] = []
    sentence_end = re.compile(r"[.!?]$")

    # Candidate words stay in spoken order so the operator reads the same visit sequence.
    for word in words:
        current_display_words.append(word)
        row_is_ready = len(current_display_words) >= max_words or bool(
            sentence_end.search(word)
        )
        # A complete sentence or row cap creates one readable transcript line.
        if row_is_ready:
            display_word_chunks.append(current_display_words)
            current_display_words = []

    # Remaining words still need a final row so the candidate transcript is complete.
    if current_display_words:
        display_word_chunks.append(current_display_words)

    return display_word_chunks


def build_history(
    *,
    transcript: str,
    audio: AudioInfo,
    model_name: str,
    source_audio: Path,
    max_words_per_segment: int,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Build the legacy transcript artifact an operator can compare and score.

    Args:
        transcript: Candidate wording; empty produces a valid report with no rows.
        audio: Fixture metadata; zero duration keeps rows at consultation start.
        model_name: Candidate ID; empty makes the report unsuitable for comparison.
        source_audio: Selected WAV; an absent path means no source can be audited.
        max_words_per_segment: Display-row cap; zero is rejected before this helper runs.
        dry_run: True validates selection/output wiring without generated wording.

    Returns:
        History-shaped evidence; empty `segments` means no candidate wording.
    """
    words = split_words(transcript)
    display_word_chunks = chunk_transcript_words(words, max_words_per_segment)
    audio_duration_seconds = max(audio.duration_seconds, 0.0)
    total_display_words = max(1, len(words))
    elapsed_display_words = 0
    transcript_rows: list[dict[str, Any]] = []

    # Each readable word group becomes one row on the operator's scoreable timeline.
    for row_index, display_words in enumerate(display_word_chunks):
        starting_word_index = elapsed_display_words
        elapsed_display_words += len(display_words)
        row_start_seconds = audio_duration_seconds * (
            starting_word_index / total_display_words
        )
        row_end_seconds = audio_duration_seconds * (
            elapsed_display_words / total_display_words
        )
        transcript_rows.append(
            {
                "segment_id": f"second_pass_{row_index:04d}",
                "speaker_id": "UNKNOWN",
                "role": "UNKNOWN",
                "start": round(row_start_seconds, 3),
                "end": round(max(row_end_seconds, row_start_seconds), 3),
                "text": " ".join(display_words),
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
        "segments": transcript_rows,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """Save deterministic legacy evidence where the operator requested it.

    Args:
        path: Evidence destination; an absent parent folder is created automatically.
        payload: Report data; an empty mapping writes a valid empty JSON object.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def parse_args() -> argparse.Namespace:
    """Read the model, consultation interval, sources, and evidence destinations.

    Returns:
        Parsed options; absent values keep full audio, TDT, and legacy mode defaults.
    """
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument(
        "--audio",
        type=Path,
        required=True,
        help="Input WAV path",
    )
    argument_parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"NeMo ASR model id (default: validated {DEFAULT_MODEL})",
    )
    argument_parser.add_argument(
        "--history-output",
        type=Path,
        required=True,
        help="Destination history-shaped JSON",
    )
    argument_parser.add_argument(
        "--metadata-output",
        type=Path,
        default=None,
        help="Optional metadata JSON destination",
    )
    argument_parser.add_argument(
        "--max-words-per-segment",
        type=int,
        default=DEFAULT_MAX_WORDS_PER_SEGMENT,
        help=f"Pseudo-segment word cap (default: {DEFAULT_MAX_WORDS_PER_SEGMENT})",
    )
    argument_parser.add_argument(
        "--seconds",
        type=float,
        default=None,
        help="Optional leading audio duration for a legacy fixture smoke",
    )
    argument_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate sources and write empty evidence without loading NeMo",
    )
    argument_parser.add_argument(
        "--application-post-visit",
        action="store_true",
        help="Use the exact stopped-visit decoder instead of the legacy model probe",
    )
    argument_parser.add_argument(
        "--correction-phrase",
        default=None,
        help="One approved corrected-lane phrase; absent keeps the baseline decoder",
    )
    argument_parser.add_argument(
        "--effective-decoder-output",
        type=Path,
        default=None,
        help="Write the effective stopped-visit decoder config after one real decode",
    )
    production.add_production_arguments(argument_parser)
    return argument_parser.parse_args()


def validate_application_post_visit_options(
    operator_options: argparse.Namespace,
) -> str | None:
    """Reject an incomplete or unreviewed phrase experiment before source access.

    Use after parsing; null return means the operator selected the frozen M02 lane.
    """
    # Legacy experiments cannot label themselves with the production decoder's phrase.
    if not operator_options.application_post_visit:
        if operator_options.correction_phrase is not None:
            return "--correction-phrase requires --application-post-visit"
        if operator_options.effective_decoder_output is not None:
            return "--effective-decoder-output requires --application-post-visit"
        return None
    # M02 compares only the pinned Unified decoder used after the clinician presses Stop.
    if operator_options.model != M02_UNIFIED_MODEL:
        return f"--application-post-visit requires {M02_UNIFIED_MODEL}"
    # A raw garble, list, or second phrase has no independent clinical review.
    if operator_options.correction_phrase not in {
        None,
        M02_APPROVED_CORRECTION_PHRASE,
    }:
        return "--correction-phrase is not the approved M02 canonical phrase"
    # A real arm without effective config evidence cannot prove its one-variable boundary.
    if (
        not operator_options.dry_run
        and operator_options.effective_decoder_output is None
    ):
        return "--application-post-visit requires --effective-decoder-output"
    # Completed decoder evidence is immutable and cannot be overwritten by a favorable rerun.
    if (
        operator_options.effective_decoder_output is not None
        and operator_options.effective_decoder_output.exists()
    ):
        return "effective decoder evidence already exists"
    return None


def run_legacy_second_pass(operator_options: argparse.Namespace) -> int:
    """Run the pre-existing explicit-fixture experiment outside production shape.

    Args:
        operator_options: Parsed model/audio/output choices; missing required paths are rejected.

    Returns:
        Exit 0 for saved evidence, 1 for model/runtime failure, or 2 for invalid input.
    """
    audio_path = operator_options.audio
    # A missing fixture cannot produce wording for the operator to compare.
    if not audio_path.exists():
        print(f"error: audio file not found: {audio_path}", file=sys.stderr)
        return 2
    # A non-positive row cap cannot create readable transcript evidence.
    if operator_options.max_words_per_segment <= 0:
        print("error: --max-words-per-segment must be positive", file=sys.stderr)
        return 2

    transcribe_path: Path | None = None
    try:
        transcribe_path, audio = clipped_wav_copy(audio_path, operator_options.seconds)
        # A dry run proves selection/output wiring without loading NeMo or wording.
        if operator_options.dry_run:
            transcript = ""
        # An application-shaped arm uses the same decoder the clinician receives after Stop.
        elif operator_options.application_post_visit:
            transcript, effective_decoding_config = (
                transcribe_application_post_visit_audio(
                    operator_options.model,
                    transcribe_path,
                    operator_options.correction_phrase,
                )
            )
        # A real legacy comparison retains its pre-existing model probe.
        else:
            transcript = transcribe_audio(operator_options.model, transcribe_path)
    # Example: the selected model cannot construct or the chosen WAV is malformed.
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        # A temporary cutoff clip is removed after its candidate evidence is assembled.
        if transcribe_path is not None and transcribe_path != audio_path:
            transcribe_path.unlink(missing_ok=True)

    history = build_history(
        transcript=transcript,
        audio=audio,
        model_name=operator_options.model,
        source_audio=audio_path,
        max_words_per_segment=operator_options.max_words_per_segment,
        dry_run=operator_options.dry_run,
    )
    write_json(operator_options.history_output, history)

    metadata = {
        "model": operator_options.model,
        "audio": history["audio"],
        "history_output": str(operator_options.history_output),
        "segment_count": len(history["segments"]),
        "word_count": len(split_words(transcript)),
        "dry_run": operator_options.dry_run,
        "seconds": operator_options.seconds,
    }
    # Requested metadata lets the operator audit the model and audio without wording.
    if operator_options.metadata_output is not None:
        write_json(operator_options.metadata_output, metadata)
    # A real application-shaped arm records the actual merged NeMo decoder config once.
    if (
        not operator_options.dry_run
        and operator_options.application_post_visit
        and operator_options.effective_decoder_output is not None
    ):
        write_json(
            operator_options.effective_decoder_output,
            effective_decoding_config,
        )

    print(
        "second-pass-asr "
        f"model={operator_options.model} "
        f"segments={metadata['segment_count']} "
        f"words={metadata['word_count']} "
        f"history={operator_options.history_output}"
    )
    return 0


def main() -> int:
    """Route one operator command to production assembly or legacy evidence.

    Returns:
        Exit 0 for saved evidence, 1 for retained failure, or 2 for invalid input.
    """
    operator_options = parse_args()
    option_error = validate_application_post_visit_options(operator_options)
    # Invalid experiment identity stops before audio, model, or output access.
    if option_error is not None:
        print(f"error: {option_error}", file=sys.stderr)
        return 2
    # Production mode owns fail-closed validation and write-once application evidence.
    if operator_options.production_shape:
        # A dry source proof must not import application NeMo code or create decoder evidence.
        if operator_options.dry_run:
            return production.run_production_shape(operator_options)
        correction_module = importlib.import_module("post_visit_correction")
        correction_module.DEFAULT_POST_VISIT_CORRECTION_PHRASE = (
            operator_options.correction_phrase
        )
        correction_module.CAPTURE_POST_VISIT_DECODING_CONFIG = True
        production_exit_code = production.run_production_shape(operator_options)
        # A successful production decode must preserve its actual merged decoder config.
        if production_exit_code == 0 and not operator_options.dry_run:
            effective_decoding_config = (
                correction_module.LAST_POST_VISIT_DECODING_CONFIG
            )
            # Missing decoder evidence invalidates an otherwise plausible transcript arm.
            if effective_decoding_config is None:
                print(
                    "error: effective decoder evidence is unavailable", file=sys.stderr
                )
                return 1
            write_json(
                operator_options.effective_decoder_output,
                effective_decoding_config,
            )
        return production_exit_code
    return run_legacy_second_pass(operator_options)


# A direct operator run creates evidence; importing helpers never loads NeMo.
if __name__ == "__main__":
    raise SystemExit(main())
