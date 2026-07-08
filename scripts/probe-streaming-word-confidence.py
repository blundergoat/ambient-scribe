#!/usr/bin/env python3
"""Probe word confidence through the REAL session-long streaming engine.

Use this developer-only spike (0.4.0 M06 phase 2 pre-wiring) before persisting
per-row confidence for live transcript rows. Phase 1 proved confidence in
OFFLINE transcribe mode only; the live path decodes step-by-step through
`StreamingSessionEngine`, so this probe runs inside the NeMo container, feeds
one fixture clip through two real engines - a control and a confidence-enabled
run - and reports whether emitted rows stay byte-identical while per-step
hypotheses expose usable word confidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import wave
from pathlib import Path
from typing import Any

DEFAULT_SECONDS = 60.0
DEFAULT_CHUNK_MS = 250
DEGENERATE_ONES_FRACTION = 0.999

# The engine and pipeline live in the agent app root inside the container.
sys.path.insert(0, "/app")


def read_clip_pcm(source_audio_path: Path, seconds_limit: float) -> bytes:
    """Read the leading PCM bytes of a fixture WAV for streaming feeds.

    Args:
        source_audio_path: Fixture WAV chosen by the developer; missing files fail before model load.
        seconds_limit: Leading seconds to stream; zero or lower means no useful audio.

    Returns:
        Raw 16 kHz mono 16-bit PCM bytes, exactly what the browser would stream.
    """
    with wave.open(str(source_audio_path), "rb") as source_audio:
        frame_limit = min(
            source_audio.getnframes(),
            int(seconds_limit * source_audio.getframerate()),
        )
        return source_audio.readframes(frame_limit)


def plain_float_list(value: Any) -> list[float]:
    """Convert a tensor/list confidence field into plain floats.

    Args:
        value: NeMo hypothesis field; None means the model omitted confidence entirely.

    Returns:
        Float list; empty means no confidence values exist for the user's transcript.
    """
    # A missing field is the no-confidence production state (findings Q6).
    if value is None:
        return []

    # Torch tensors need to move off GPU before JSON-style inspection.
    if hasattr(value, "detach"):
        value = value.detach().cpu()
    # Tensor-like values become plain lists so the report code sees one shape.
    if hasattr(value, "tolist"):
        value = value.tolist()

    # Values that cannot become numbers count as no confidence evidence.
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        return []


def confidence_stats(values: list[float]) -> dict[str, Any]:
    """Summarize one confidence list for the streaming GO/NO-GO decision.

    Args:
        values: Per-word confidences; empty means the field stayed unavailable.

    Returns:
        Count/min/max/mean plus the fraction of ~1.0 values (all-ones = degenerate,
        useless for low-confidence row styling in the transcript UI).
    """
    # No values means streaming decode did not surface confidence.
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

    Mirrors the phase-1 offline probe and the intended runtime wiring exactly,
    including reapplying the CUDA-graph workaround the strategy rebuild resets.

    Args:
        asr_model: Loaded multitalker model shared by the streaming engines.

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

    # The strategy rebuild replaces the decoder internals, so the runtime's
    # PyTorch 2.8 CUDA-graph workaround must be applied again afterwards.
    asr_model.decoding.decoding.use_cuda_graph_decoder = False
    asr_model.decoding.decoding.decoding_computer.disable_cuda_graphs()

    return confidence_settings


def row_signature(rows: list[Any]) -> dict[str, Any]:
    """Build a comparable signature for one engine run's emitted rows.

    Args:
        rows: EngineRow list from feed/flush calls; empty means no speech emitted.

    Returns:
        Row count, a SHA-256 over slot/text/timing, and a short preview for the report.
    """
    # Only slot/text/timing enter the hash - the fields a decode change would move.
    canonical_rows = [
        (row.speaker_slot, row.text, round(row.start, 3), round(row.end, 3))
        for row in rows
    ]
    signature = hashlib.sha256(
        json.dumps(canonical_rows, sort_keys=True).encode("utf-8")
    ).hexdigest()
    # The first few rows give the report reader a human-checkable transcript peek.
    return {
        "rows": len(canonical_rows),
        "sha256": signature,
        "preview": [
            f"{slot} [{start}-{end}] {text[:60]}"
            for slot, text, start, end in canonical_rows[:8]
        ],
    }


def inspect_step_hypotheses(engine: Any, slot_evidence: dict[int, dict[str, int]]) -> None:
    """Record word/confidence alignment for every live per-slot hypothesis.

    The engine's word log splits `hypothesis.text` per step, so persisted
    confidence must align index-by-index with those words. Each inspection
    counts one step as aligned or misaligned per slot.

    Args:
        engine: Streaming engine whose composite holds per-slot hypotheses.
        slot_evidence: Mutable per-slot counters updated in place; missing slots are created.
    """
    asr_states = getattr(engine._streamer.instance_manager, "batch_asr_states", [])
    # No ASR state means the composite has not decoded its first step yet.
    if not asr_states:
        return

    hypotheses = getattr(asr_states[0], "previous_hypothesis", None) or []
    # Each slot's hypothesis is the exact object the engine's word log reads.
    for slot_index, hypothesis in enumerate(hypotheses):
        # Empty slots have no words the transcript UI could style yet.
        if hypothesis is None or not getattr(hypothesis, "text", None):
            continue

        words = str(hypothesis.text).split()
        word_values = plain_float_list(getattr(hypothesis, "word_confidence", None))
        evidence = slot_evidence.setdefault(
            slot_index,
            {"aligned_steps": 0, "misaligned_steps": 0, "empty_confidence_steps": 0},
        )

        # Missing confidence on a live hypothesis means the streaming decode path
        # never computed it - the exact unknown this probe exists to answer.
        if word_values == []:
            evidence["empty_confidence_steps"] += 1
            continue

        # Persistence joins confidence to words by index, so lengths must agree.
        if len(word_values) == len(words):
            evidence["aligned_steps"] += 1
        else:
            evidence["misaligned_steps"] += 1


def run_streaming_pass(
    pipeline: Any,
    session_label: str,
    pcm_audio: bytes,
    chunk_bytes: int,
    collect_confidence: bool,
) -> dict[str, Any]:
    """Stream one clip through a fresh engine and collect emission evidence.

    Args:
        pipeline: Loaded NemoPipeline owning the shared models.
        session_label: Probe session id recorded in engine logs.
        pcm_audio: Full clip PCM; empty produces an empty-row report.
        chunk_bytes: Bytes per feed call, mirroring the browser chunk cadence.
        collect_confidence: True also inspects per-step hypothesis confidence.

    Returns:
        Row signature plus per-slot confidence evidence for the report.
    """
    engine = pipeline.create_streaming_engine(session_label)
    emitted_rows: list[Any] = []
    slot_evidence: dict[int, dict[str, int]] = {}
    final_slot_confidence: dict[str, Any] = {}

    try:
        # Uniform chunks mirror the eval harness cadence; the probe tests the
        # decode mechanism, not cadence shape (that is M02's separate lane).
        for chunk_start in range(0, len(pcm_audio), chunk_bytes):
            emitted_rows.extend(
                engine.feed(pcm_audio[chunk_start : chunk_start + chunk_bytes])
            )
            # Confidence evidence is read per feed so revisions are covered too.
            if collect_confidence:
                inspect_step_hypotheses(engine, slot_evidence)

        emitted_rows.extend(engine.flush())

        # The final hypotheses carry the full-session confidence lists the
        # persistence layer would aggregate per row.
        if collect_confidence:
            asr_states = getattr(engine._streamer.instance_manager, "batch_asr_states", [])
            hypotheses = (
                getattr(asr_states[0], "previous_hypothesis", None) or [] if asr_states else []
            )
            # Each speaker slot contributes its own confidence summary to the report.
            for slot_index, hypothesis in enumerate(hypotheses):
                # Slots that never spoke have nothing to summarize.
                if hypothesis is None or not getattr(hypothesis, "text", None):
                    continue
                final_slot_confidence[f"slot_{slot_index}"] = {
                    "words": len(str(hypothesis.text).split()),
                    "word_confidence": confidence_stats(
                        plain_float_list(getattr(hypothesis, "word_confidence", None))
                    ),
                }
    finally:
        engine.close()

    report: dict[str, Any] = {"row_signature": row_signature(emitted_rows)}
    # Confidence evidence only exists for the enabled pass.
    if collect_confidence:
        report["per_step_alignment"] = {
            f"slot_{slot_index}": counters for slot_index, counters in slot_evidence.items()
        }
        report["final_slot_confidence"] = final_slot_confidence

    return report


def run_streaming_confidence_probe(
    audio_path: Path,
    seconds_limit: float,
    chunk_ms: int,
) -> dict[str, Any]:
    """Run the control and confidence-enabled streaming passes and compare them.

    Args:
        audio_path: Fixture WAV path available inside the container.
        seconds_limit: Leading seconds to stream; zero or lower is rejected before model load.
        chunk_ms: Feed cadence in milliseconds; must be positive.

    Returns:
        JSON report with row-identity comparison and confidence statistics.

    Raises:
        FileNotFoundError: When the selected fixture path is unavailable to the container.
        ValueError: When the clip duration or chunk size cannot drive a stream.
        RuntimeError: When NeMo models are unavailable in this process.
    """
    # The probe needs a real clip; otherwise a missing-confidence result is meaningless.
    if not audio_path.exists():
        raise FileNotFoundError(f"audio file not found: {audio_path}")

    # A non-positive probe would stream an empty clip and prove nothing.
    if seconds_limit <= 0:
        raise ValueError("--seconds must be positive")

    # Zero-size chunks could never simulate a browser sending audio.
    if chunk_ms <= 0:
        raise ValueError("--chunk-ms must be positive")

    from nemo_pipeline import NemoPipeline

    pipeline = NemoPipeline()
    # Without loaded models the engine factory refuses to build sessions.
    if not pipeline.is_loaded:
        raise RuntimeError(f"NeMo models failed to load: {pipeline.load_error}")

    pcm_audio = read_clip_pcm(audio_path, seconds_limit)
    chunk_bytes = int(16000 * 2 * (chunk_ms / 1000.0))

    # The control pass captures today's emission behavior on this exact model.
    control_report = run_streaming_pass(
        pipeline, "probe-streaming-control", pcm_audio, chunk_bytes, collect_confidence=False
    )
    applied_confidence_settings = enable_confidence_decoding(pipeline._asr_model)
    confidence_report = run_streaming_pass(
        pipeline, "probe-streaming-confidence", pcm_audio, chunk_bytes, collect_confidence=True
    )

    final_confidences = confidence_report.get("final_slot_confidence", {})
    # Only slots that produced measured words can vote on degeneracy.
    ones_fractions = [
        slot["word_confidence"].get("ones_fraction", 1.0)
        for slot in final_confidences.values()
        if slot["word_confidence"].get("count", 0) > 0
    ]
    # GO needs real values that are not uniformly ~1.0 - all-ones cannot drive
    # the low-confidence row styling the summary UX task was blocked on.
    non_degenerate = any(
        fraction < DEGENERATE_ONES_FRACTION for fraction in ones_fractions
    )

    return {
        "audio": str(audio_path),
        "seconds": seconds_limit,
        "chunk_ms": chunk_ms,
        "confidence_settings": applied_confidence_settings,
        "control": control_report,
        "confidence": confidence_report,
        "rows_byte_identical": (
            control_report["row_signature"]["sha256"]
            == confidence_report["row_signature"]["sha256"]
        ),
        "non_degenerate_word_confidence": non_degenerate,
    }


def parse_args() -> argparse.Namespace:
    """Parse streaming confidence probe arguments for a local developer run.

    Returns:
        Parsed CLI options; a missing audio path exits through argparse before probing.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, required=True, help="WAV path inside the container")
    parser.add_argument("--seconds", type=float, default=DEFAULT_SECONDS, help="Leading seconds to stream")
    parser.add_argument("--chunk-ms", type=int, default=DEFAULT_CHUNK_MS, help="Feed cadence in milliseconds")
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON report path")
    return parser.parse_args()


def main() -> int:
    """Run the probe and print or save the JSON report.

    Returns:
        Process exit code; zero means the streaming report was written or printed.
    """
    args = parse_args()
    report = run_streaming_confidence_probe(args.audio, args.seconds, args.chunk_ms)
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
