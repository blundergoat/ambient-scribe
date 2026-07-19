#!/usr/bin/env python3
"""Probe whether NeMo ASR word/token confidence is obtainable for one fixture clip.

Use this developer-only spike (0.4.0 M06 phase 1) before persisting per-row
confidence for the transcript UI. It runs inside the NeMo container, loads the
selected ASR model with confidence enabled in the decoding config, transcribes
a short fixture clip, and reports confidence-value statistics so a GO/NO-GO
can be recorded without touching the serving agent, browser, or storage.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import wave
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "nvidia/parakeet-unified-en-0.6b"
DEFAULT_SECONDS = 20.0
DEGENERATE_ONES_FRACTION = 0.999


def clip_wav_for_probe(source_audio_path: Path, seconds_limit: float) -> Path:
    """Create a short WAV clip so the confidence probe finishes quickly.

    Args:
        source_audio_path: Fixture WAV chosen by the developer; missing files fail before model load.
        seconds_limit: Leading seconds to inspect; zero or lower means no useful audio.

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


def plain_float_list(value: Any) -> list[float]:
    """Convert a tensor/list confidence field into plain floats.

    Args:
        value: NeMo hypothesis field; None means the model omitted confidence entirely.

    Returns:
        Float list; empty means no confidence values exist for the user's transcript.
    """
    # A missing field is the current production state (findings Q6).
    if value is None:
        return []

    # Torch tensors need to move off GPU before JSON-style inspection.
    if hasattr(value, "detach"):
        value = value.detach().cpu()
    if hasattr(value, "tolist"):
        value = value.tolist()

    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        return []


def confidence_stats(values: list[float]) -> dict[str, Any]:
    """Summarize one confidence list for the GO/NO-GO decision.

    Args:
        values: Per-word or per-token confidences; empty means the field stayed unavailable.

    Returns:
        Count/min/max/mean plus the fraction of ~1.0 values (all-ones = degenerate,
        useless for low-confidence styling in the transcript UI).
    """
    # No values means the decoding config change did not surface confidence.
    if not values:
        return {"count": 0}

    ones = sum(1 for value in values if value >= DEGENERATE_ONES_FRACTION)
    return {
        "count": len(values),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "mean": round(sum(values) / len(values), 4),
        "ones_fraction": round(ones / len(values), 4),
        "sample": [round(value, 4) for value in values[:12]],
    }


def enable_confidence_decoding(asr_model: Any) -> dict[str, Any]:
    """Turn on word/token confidence in the model's decoding config.

    Args:
        asr_model: Loaded NeMo ASR model about to transcribe the probe clip.

    Returns:
        The confidence config applied, echoed into the report for reproducibility.
    """
    from omegaconf import OmegaConf, open_dict

    confidence_settings = {
        "preserve_frame_confidence": False,
        "preserve_token_confidence": True,
        "preserve_word_confidence": True,
        "exclude_blank": True,
        "aggregation": "min",
        "method_cfg": {"name": "max_prob"},
    }
    decoding_config = OmegaConf.create(
        OmegaConf.to_container(asr_model.cfg.decoding, resolve=True)
    )
    with open_dict(decoding_config):
        # NeMo reads exactly this key; a different name is silently ignored.
        decoding_config.confidence_cfg = confidence_settings
    asr_model.change_decoding_strategy(decoding_config)

    return confidence_settings


def did_apply_multitalker_cuda_graph_workaround(asr_model: Any) -> bool:
    """Mirror the runtime's CUDA-graph workaround on the live multitalker model.

    The serving pipeline disables CUDA graph decoding for PyTorch 2.8 compat;
    the probe must test the model in the same configuration the product runs.

    Args:
        asr_model: Loaded multitalker model, after any decoding-strategy change.

    Returns:
        True when the workaround attributes existed and were applied.
    """
    try:
        asr_model.decoding.decoding.use_cuda_graph_decoder = False
        asr_model.decoding.decoding.decoding_computer.disable_cuda_graphs()
        return True
    except AttributeError:
        # A rebuilt decoding strategy may not expose the same internals; the
        # report records this so a crash can be attributed correctly.
        return False


def flatten_hypotheses(result: Any) -> list[Any]:
    """Flatten transcribe() output that may nest per-speaker hypothesis lists.

    Args:
        result: Return value of `transcribe(...)`; None means the model produced nothing.

    Returns:
        Flat hypothesis list; empty means the clip yielded no transcript at all.
    """
    # No result means the user would see an empty transcript for this clip.
    if result is None:
        return []

    flat: list[Any] = []
    for item in result:
        # Multitalker models can return one hypothesis list per speaker slot.
        if isinstance(item, list):
            flat.extend(entry for entry in item if entry is not None)
        elif item is not None:
            flat.append(item)

    return flat


def run_confidence_probe(
    audio_path: Path,
    model_name: str,
    seconds_limit: float,
    is_multitalker: bool,
) -> dict[str, Any]:
    """Run one offline ASR call with confidence enabled and report the outcome.

    Args:
        audio_path: Fixture WAV path available inside the container.
        model_name: ASR checkpoint to probe; empty would make model loading ambiguous.
        seconds_limit: Leading seconds to inspect; zero or lower is rejected before model load.
        is_multitalker: True mirrors the live pipeline's CUDA-graph workaround.

    Returns:
        JSON report with per-hypothesis word/token confidence statistics.

    Raises:
        FileNotFoundError: When the selected fixture path is unavailable to the container.
        ValueError: When the requested clip has no positive duration to inspect.
    """
    # The probe needs a real clip; otherwise a missing-confidence result is meaningless.
    if not audio_path.exists():
        raise FileNotFoundError(f"audio file not found: {audio_path}")

    # A non-positive probe would test an empty clip and prove nothing.
    if seconds_limit <= 0:
        raise ValueError("--seconds must be positive")

    import nemo.collections.asr as nemo_asr

    clipped_audio_path = clip_wav_for_probe(audio_path, seconds_limit)
    try:
        asr_model = nemo_asr.models.ASRModel.from_pretrained(model_name=model_name)
        applied_confidence_settings = enable_confidence_decoding(asr_model)
        workaround_applied = (
            did_apply_multitalker_cuda_graph_workaround(asr_model) if is_multitalker else None
        )
        result = asr_model.transcribe(
            [str(clipped_audio_path)],
            return_hypotheses=True,
        )
    finally:
        clipped_audio_path.unlink(missing_ok=True)

    hypotheses = flatten_hypotheses(result)
    per_hypothesis: list[dict[str, Any]] = []
    # Each hypothesis is one speaker/stream the transcript UI could style rows for.
    for hypothesis in hypotheses:
        word_values = plain_float_list(getattr(hypothesis, "word_confidence", None))
        token_values = plain_float_list(getattr(hypothesis, "token_confidence", None))
        per_hypothesis.append(
            {
                "text_preview": str(getattr(hypothesis, "text", ""))[:120],
                "word_confidence": confidence_stats(word_values),
                "token_confidence": confidence_stats(token_values),
            }
        )

    word_counts = [entry["word_confidence"].get("count", 0) for entry in per_hypothesis]
    ones_fractions = [
        entry["word_confidence"].get("ones_fraction", 1.0)
        for entry in per_hypothesis
        if entry["word_confidence"].get("count", 0) > 0
    ]
    # GO needs real values that are not uniformly ~1.0 - all-ones cannot drive
    # the low-confidence row styling the summary UX task was blocked on.
    non_degenerate = sum(word_counts) > 0 and any(
        fraction < DEGENERATE_ONES_FRACTION for fraction in ones_fractions
    )

    return {
        "model": model_name,
        "audio": str(audio_path),
        "seconds": seconds_limit,
        "confidence_settings": applied_confidence_settings,
        "multitalker_cuda_graph_workaround": workaround_applied,
        "hypothesis_count": len(hypotheses),
        "per_hypothesis": per_hypothesis,
        "non_degenerate_word_confidence": non_degenerate,
    }


def parse_args() -> argparse.Namespace:
    """Parse confidence probe arguments for a local developer run.

    Returns:
        Parsed CLI options; a missing audio path exits through argparse before probing.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, required=True, help="WAV path inside the container")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"ASR model id, default {DEFAULT_MODEL}")
    parser.add_argument("--seconds", type=float, default=DEFAULT_SECONDS, help="Leading seconds to inspect")
    parser.add_argument(
        "--multitalker",
        action="store_true",
        help="Apply the live pipeline's CUDA-graph workaround (multitalker models)",
    )
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON report path")
    return parser.parse_args()


def main() -> int:
    """Run the probe and print or save the JSON report.

    Returns:
        Process exit code; zero means the confidence report was written or printed.
    """
    args = parse_args()
    report = run_confidence_probe(args.audio, args.model, args.seconds, args.multitalker)
    serialized_report = json.dumps(report, sort_keys=True, indent=2) + "\n"

    # A provided output path lets spike evidence land beside other QA artifacts.
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized_report, encoding="utf-8")
    else:
        print(serialized_report, end="")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
