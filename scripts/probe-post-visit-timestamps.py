#!/usr/bin/env python3
"""Inspect post-visit ASR hypothesis timing fields for one fixture clip.

Use this developer-only probe after a consultation replay shows a corrected
transcript issue that needs word timing. It runs inside the NeMo container,
loads the configured offline ASR model, and writes a JSON shape report without
changing browser, FastAPI, or correction storage contracts.
"""

from __future__ import annotations

import argparse
import inspect
import json
import tempfile
import wave
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "nvidia/parakeet-unified-en-0.6b"
DEFAULT_SECONDS = 15.0
MAX_PREVIEW_CHARS = 300


def clip_wav_for_probe(source_audio_path: Path, seconds_limit: float) -> Path:
    """Create a short WAV clip so the timestamp probe finishes quickly.

    Args:
        source_audio_path: Fixture WAV selected by the developer; missing files fail before model load.
        seconds_limit: Leading seconds to inspect; zero or lower means the probe has no useful audio.

    Returns:
        Temporary WAV path for NeMo ASR; caller deletes it after probing.
    """
    with wave.open(str(source_audio_path), "rb") as source_audio:
        frame_limit = min(
            source_audio.getnframes(),
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


def wav_duration_seconds(audio_path: Path) -> float:
    """Return the clip duration used for timestamp-to-seconds conversion.

    Args:
        audio_path: WAV path the model transcribes; missing or invalid files fail the probe.

    Returns:
        Duration in seconds; zero means the clip has no scoreable audio.
    """
    with wave.open(str(audio_path), "rb") as audio_file:
        sample_rate = audio_file.getframerate()
        # A zero sample rate means the file cannot provide user-visible timing.
        if sample_rate == 0:
            return 0.0

        return audio_file.getnframes() / sample_rate


def summarize_hypothesis_value(value: Any) -> dict[str, Any]:
    """Summarize one ASR hypothesis attribute without dumping huge tensors.

    Args:
        value: Attribute value from the NeMo hypothesis; null means the model omitted that field.

    Returns:
        JSON-safe type, length, and preview data for the timestamp report.
    """
    value_length: int | None
    try:
        value_length = len(value)
    except TypeError:
        value_length = None

    return {
        "type": type(value).__name__,
        "len": value_length,
        "preview": str(value)[:MAX_PREVIEW_CHARS],
    }


def inspect_hypothesis_shape(hypothesis: Any) -> dict[str, Any]:
    """Return visible fields from the first NeMo hypothesis.

    Args:
        hypothesis: First item from `transcribe(..., return_hypotheses=True)`; null means no result.

    Returns:
        Attribute shape report; empty means the model produced no hypothesis object.
    """
    # No hypothesis means the user would get no corrected transcript from this clip.
    if hypothesis is None:
        return {}

    attributes: dict[str, Any] = {}
    # Each public, non-callable attribute is a possible source of timing evidence.
    for attribute_name in dir(hypothesis):
        # Private implementation details are too unstable for a product path.
        if attribute_name.startswith("_"):
            continue

        try:
            attribute_value = getattr(hypothesis, attribute_name)
        except Exception as attribute_error:
            attributes[attribute_name] = {"error": str(attribute_error)}
            continue

        # Methods are API surface, not the timestamp data this probe needs.
        if callable(attribute_value):
            continue

        attributes[attribute_name] = summarize_hypothesis_value(attribute_value)

    return attributes


def list_from_hypothesis_value(value: Any) -> list[Any]:
    """Convert tensor/list hypothesis values into plain JSON-friendly lists.

    Args:
        value: NeMo hypothesis field; null means this model omitted the field.

    Returns:
        Plain list; empty means no iterable timing or word data exists.
    """
    # Missing fields cannot drive a word-level corrected transcript.
    if value is None:
        return []

    # Torch tensors need to move off GPU before JSON-style inspection.
    if hasattr(value, "detach"):
        value = value.detach().cpu()

    # Tensor-like objects expose tolist(), which preserves numeric order.
    if hasattr(value, "tolist"):
        return value.tolist()

    # Already-list fields such as hypothesis.words can be used directly.
    if isinstance(value, list):
        return value

    try:
        return list(value)
    except TypeError:
        return []


def scalar_float_from_hypothesis_value(value: Any) -> float | None:
    """Convert scalar tensor or number fields into a float.

    Args:
        value: NeMo scalar field; null means the model omitted that timing denominator.

    Returns:
        Float value, or None when the field is unavailable or not numeric.
    """
    # Missing scalar data means timestamp units cannot be converted to seconds.
    if value is None:
        return None

    # Tensor scalars expose item(); detach first in case the value lives on GPU.
    if hasattr(value, "detach"):
        value = value.detach().cpu()

    # Tensor scalar item() gives the actual numeric denominator.
    if hasattr(value, "item"):
        value = value.item()

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def estimate_word_timings(hypothesis: Any, audio_duration_seconds: float) -> list[dict[str, Any]]:
    """Estimate word timings from NeMo token timestamps.

    Args:
        hypothesis: NeMo ASR hypothesis; null means no corrected words exist for the user.
        audio_duration_seconds: Clip duration; zero means timing cannot be converted to seconds.

    Returns:
        Word timing rows with estimated starts and ends; empty means timing is unavailable.
    """
    # No hypothesis means there is no word evidence to align to live rows.
    if hypothesis is None:
        return []

    words = [str(word) for word in list_from_hypothesis_value(getattr(hypothesis, "words", None))]
    timestamp_field = getattr(hypothesis, "timestamp", None)
    # With timestamps=True the field is a word/segment mapping, not the token-frame tensor
    # this estimator was built for; native entries are reported separately.
    if isinstance(timestamp_field, dict):
        return []

    token_timestamps = [
        float(timestamp)
        for timestamp in list_from_hypothesis_value(timestamp_field)
    ]
    frame_count = scalar_float_from_hypothesis_value(getattr(hypothesis, "length", None))

    # All three fields are needed before the artifact can claim word timing.
    if words == [] or token_timestamps == [] or frame_count is None or frame_count <= 0:
        return []

    seconds_per_frame = audio_duration_seconds / frame_count
    word_timings: list[dict[str, Any]] = []
    # Tokens outnumber words, so distribute token timestamps across words proportionally.
    for word_index, word in enumerate(words):
        token_start_index = int(word_index * len(token_timestamps) / len(words))
        token_end_index = int((word_index + 1) * len(token_timestamps) / len(words))
        token_end_index = min(
            max(token_end_index, token_start_index + 1),
            len(token_timestamps),
        )
        token_start_frame = token_timestamps[token_start_index]
        token_end_frame = token_timestamps[token_end_index - 1] + 1.0
        word_timings.append(
            {
                "index": word_index,
                "word": word,
                "start": round(token_start_frame * seconds_per_frame, 3),
                "end": round(max(token_end_frame, token_start_frame + 1.0) * seconds_per_frame, 3),
                "token_start_index": token_start_index,
                "token_end_index": token_end_index,
                "token_start_frame": token_start_frame,
                "token_end_frame": token_end_frame,
            }
        )

    return word_timings


def native_word_timings(hypothesis: Any) -> list[dict[str, Any]]:
    """Extract NeMo's own word-level timestamps when `timestamps=True` was requested.

    Args:
        hypothesis: NeMo ASR hypothesis; None or a tensor-shaped `timestamp` field means the
            model produced no native word timing for this clip.

    Returns:
        Raw word entries as NeMo reports them; empty means only estimated timing exists.
    """
    timestamp_field = getattr(hypothesis, "timestamp", None)
    # Without the timestamps flag the field is a token tensor, not a word-entry mapping.
    if not isinstance(timestamp_field, dict):
        return []

    word_entries = timestamp_field.get("word", [])
    # A non-list word field means this NeMo version reports timing another way.
    if not isinstance(word_entries, list):
        return []

    native_timings: list[dict[str, Any]] = []
    # Each entry is kept verbatim so the report shows exactly which keys this model emits.
    for entry in word_entries:
        # Non-dict entries would hide the schema the runtime needs to rely on.
        if isinstance(entry, dict):
            native_timings.append(dict(entry))

    return native_timings


def run_timestamp_probe(
    audio_path: Path,
    model_name: str,
    seconds_limit: float,
    request_timestamps: bool = False,
) -> dict[str, Any]:
    """Run one offline ASR call and report whether timing fields exist.

    Args:
        audio_path: Fixture WAV path available inside the NeMo container.
        model_name: ASR checkpoint to inspect; empty would make model loading ambiguous.
        seconds_limit: Leading seconds to inspect; zero or lower is rejected before model load.
        request_timestamps: True also asks NeMo for native word/segment timestamps; false keeps
            the original token-tensor behaviour for comparison runs.

    Returns:
        JSON report with transcribe signature, hypothesis shape, and timing-field summary.

    Raises:
        FileNotFoundError: When the selected fixture path is unavailable to the container.
        ValueError: When the requested clip has no positive duration for the user to inspect.
    """
    # The probe must have a real clip; otherwise any missing timestamp result is meaningless.
    if not audio_path.exists():
        raise FileNotFoundError(f"audio file not found: {audio_path}")

    # A non-positive probe would create an empty correction candidate.
    if seconds_limit <= 0:
        raise ValueError("--seconds must be positive")

    import nemo.collections.asr as nemo_asr

    clipped_audio_path = clip_wav_for_probe(audio_path, seconds_limit)
    try:
        audio_duration = wav_duration_seconds(clipped_audio_path)
        asr_model = nemo_asr.models.ASRModel.from_pretrained(model_name=model_name)
        transcribe_signature = str(inspect.signature(asr_model.transcribe))
        transcribe_kwargs: dict[str, Any] = {"return_hypotheses": True}
        # Native timestamps are requested only when asked, so old reports stay comparable.
        if request_timestamps:
            transcribe_kwargs["timestamps"] = True
        result = asr_model.transcribe(
            [str(clipped_audio_path)],
            **transcribe_kwargs,
        )
    finally:
        clipped_audio_path.unlink(missing_ok=True)

    hypothesis = result[0] if result else None
    attributes = inspect_hypothesis_shape(hypothesis)
    timestamp_report = attributes.get("timestamp")
    word_timings = estimate_word_timings(hypothesis, audio_duration)
    native_timings = native_word_timings(hypothesis)

    return {
        "model": model_name,
        "audio": str(audio_path),
        "seconds": seconds_limit,
        "audio_duration_seconds": audio_duration,
        "transcribe_signature": transcribe_signature,
        "result_type": type(result).__name__,
        "result_len": len(result) if result is not None else None,
        "hypothesis_type": type(hypothesis).__name__ if hypothesis is not None else None,
        "has_timestamp_attr": timestamp_report is not None,
        "timestamp_len": timestamp_report.get("len") if timestamp_report else None,
        "requested_native_timestamps": request_timestamps,
        "native_word_timing_count": len(native_timings),
        "native_word_timings": native_timings,
        "word_timing_strategy": "token-timestamps-proportional-to-words",
        "word_timing_count": len(word_timings),
        "word_timings": word_timings,
        "attributes": attributes,
    }


def parse_args() -> argparse.Namespace:
    """Parse timestamp probe arguments for a local developer run.

    Returns:
        Parsed CLI options; missing required audio path exits through argparse before probing.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, required=True, help="WAV path inside the container")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"ASR model id, default {DEFAULT_MODEL}")
    parser.add_argument("--seconds", type=float, default=DEFAULT_SECONDS, help="Leading seconds to inspect")
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON report path")
    parser.add_argument(
        "--timestamps",
        action="store_true",
        help="Also request NeMo native word/segment timestamps (timestamps=True)",
    )
    return parser.parse_args()


def main() -> int:
    """Run the probe and print or save the JSON report.

    Returns:
        Process exit code; zero means the timestamp report was written or printed.
    """
    args = parse_args()
    report = run_timestamp_probe(args.audio, args.model, args.seconds, args.timestamps)
    serialized_report = json.dumps(report, sort_keys=True, indent=2) + "\n"

    # A provided output path lets fixture eval scripts collect the report later.
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized_report, encoding="utf-8")
    else:
        print(serialized_report, end="")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
