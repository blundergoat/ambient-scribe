"""Keep production-shaped post-visit evaluation fail-closed and auditable.

The command runner uses these helpers after an operator selects the frozen ten
development consultations. They validate copied audio and visible transcript
rows, call the same correction assembly used after Stop, and retain an empty
artifact whenever the candidate would leave the clinician on live fallback.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import re
import sys
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEVELOPMENT_MANIFEST_NAME = "development-corpus-0.5.0.json"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
EXPECTED_DEVELOPMENT_STEMS = (
    "primock57-day1-consultation02-i-have-sore-red-skin",
    "primock57-day1-consultation03-i-have-terrible-headache",
    "primock57-day1-consultation06-hard-to-breathe",
    "primock57-day1-consultation07-i-have-a-cough-and-cold",
    "primock57-day1-consultation08-i-have-dry-itchy-skin",
    "primock57-day2-consultation03-i-cant-hear-very-well-and-my-face-is-a-bit-numb",
    "primock57-day2-consultation09-i-cant-move-my-left-arm",
    "primock57-day3-consultation01-lips-swelling-after-eating-a-sandwich",
    "primock57-day5-consultation03-im-feeling-very-anxious",
    "primock57-day5-consultation09-tired-all-the-time",
)


@dataclass(frozen=True)
class ProductionAudioInfo:
    """Describe copied consultation audio used by production correction.

    Operators use this to prove candidate rows came from the same browser-format
    audio that a clinician would submit after pressing Stop.

    Attributes:
        duration_seconds: Playable length; zero means no audible content was accepted.
        sample_rate: Samples per second; zero means the WAV has no usable timeline.
        channels: Recorded channel count; zero means format metadata was invalid.
        sample_width: Bytes per sample; zero means format metadata was invalid.
    """

    duration_seconds: float
    sample_rate: int
    channels: int
    sample_width: int


@dataclass
class ProductionRunEvidence:
    """Carry one production-shaped case from source checks to its audit files.

    Operators use the nullable fields to distinguish a rejected source, a
    model failure, and a successful corrected transcript without guessing.

    Attributes:
        audio: Validated WAV details; None means audio was not safe to decode.
        audio_sha256_before: Pre-run audio digest; None means audio was not opened.
        audio_sha256_after: Post-run audio digest; None means rechecking was unavailable.
        live_history: Parsed visible-row scaffold; empty means it was not accepted.
        live_history_sha256_before: Pre-run scaffold digest; None means it was not opened.
        live_history_sha256_after: Post-run scaffold digest; None means rechecking failed.
        manifest_sha256_before: Pre-run corpus-contract digest; None means it was not accepted.
        manifest_sha256_after: Post-run corpus-contract digest; None means rechecking failed.
        correction_result: Application correction result; None means no correction was returned.
        production_error: Exact failed operation; None means no exception was caught.
        module_origin: Imported application module path; None means it was never imported.
        status: `success`, `dry_run`, or `failed` for this exact consultation.
        reason_category: Stable failure label; None means this case did not fail.
    """

    audio: ProductionAudioInfo | None = None
    audio_sha256_before: str | None = None
    audio_sha256_after: str | None = None
    live_history: dict[str, Any] = field(default_factory=dict)
    live_history_sha256_before: str | None = None
    live_history_sha256_after: str | None = None
    manifest_sha256_before: str | None = None
    manifest_sha256_after: str | None = None
    correction_result: Any | None = None
    production_error: Exception | None = None
    module_origin: str | None = None
    status: str = "failed"
    reason_category: str | None = None


def file_sha256(file_path: Path) -> str:
    """Hash one approved evaluator input before or after the user-facing run.

    Args:
        file_path: Approved source; an absent path raises instead of returning empty evidence.

    Returns:
        Lowercase SHA-256; an empty file returns its standard non-empty digest.
    """
    file_digest = hashlib.sha256()
    with file_path.open("rb") as input_file:
        # Bounded reads keep a long consultation from consuming excess operator memory.
        while input_chunk := input_file.read(1024 * 1024):
            file_digest.update(input_chunk)
    return file_digest.hexdigest()


def validate_production_corpus_selection(requested_stems: list[str]) -> None:
    """Reject anything except the exact ordered ten-case development campaign.

    Args:
        requested_stems: Operator selections; empty means no authorized campaign was supplied.

    Raises:
        ValueError: A case is missing, extra, implicit, sealed, or out of order.
    """
    # An implicit all-corpus request could include sealed consultations without showing them.
    if "--all" in requested_stems:
        raise ValueError(
            "--all is forbidden; pass the ten development stems explicitly"
        )
    # A complete comparison needs all ten fixed cases, never a favorable subset.
    if len(requested_stems) != len(EXPECTED_DEVELOPMENT_STEMS):
        raise ValueError("production shape requires exactly ten fixture stems")
    # Each visible position must match so baseline and candidate rows stay comparable.
    for fixture_position, (requested_stem, expected_stem) in enumerate(
        zip(requested_stems, EXPECTED_DEVELOPMENT_STEMS, strict=True),
        start=1,
    ):
        # A different name at this position blocks source access for the whole campaign.
        if requested_stem != expected_stem:
            raise ValueError(
                f"production fixture order differs at position {fixture_position}"
            )


def verify_source_identity(
    *,
    source_path: Path,
    expected_bytes: int,
    expected_sha256: str,
    source_label: str,
) -> str:
    """Prove one copied campaign source still has its frozen host identity.

    Args:
        source_path: Explicit input; an absent path means the case cannot reach correction.
        expected_bytes: Frozen byte count; zero permits only an intentionally empty source.
        expected_sha256: Frozen digest; empty or malformed text authorizes no source.
        source_label: Safe evidence name; empty would make a rejection hard to diagnose.

    Returns:
        Verified lowercase SHA-256; it is never empty after a successful check.

    Raises:
        ValueError: The path, byte size, or digest does not match the approved campaign.
    """
    # A missing copied source means the container did not receive this consultation safely.
    if not source_path.is_file():
        raise ValueError(f"missing_file: {source_label}")
    # A malformed expected digest cannot bind this run to frozen source content.
    if SHA256_PATTERN.fullmatch(expected_sha256) is None:
        raise ValueError(f"invalid_identity: {source_label} SHA-256")
    # A changed size proves this is not the source accepted by the host-side gate.
    if source_path.stat().st_size != expected_bytes:
        raise ValueError(f"size_drift: {source_label}")
    actual_source_sha256 = file_sha256(source_path)
    # Equal-sized changed content still invalidates the user's model comparison.
    if actual_source_sha256 != expected_sha256:
        raise ValueError(f"hash_drift: {source_label}")
    return actual_source_sha256


def read_production_audio(
    *,
    audio_path: Path,
    expected_bytes: int,
    expected_sha256: str,
) -> tuple[bytes, ProductionAudioInfo, str]:
    """Read exact browser-format PCM for the application's correction seam.

    Args:
        audio_path: Copied development WAV; absent means no consultation can be corrected.
        expected_bytes: Host-verified byte count; zero cannot contain playable PCM.
        expected_sha256: Host-verified digest; empty authorizes no audio content.

    Returns:
        Non-empty PCM, WAV details, and verified digest for the operator's evidence.

    Raises:
        ValueError: Audio identity, format, or PCM content is unsafe for comparison.
        wave.Error: The copied consultation is not a readable WAV file.
    """
    audio_sha256 = verify_source_identity(
        source_path=audio_path,
        expected_bytes=expected_bytes,
        expected_sha256=expected_sha256,
        source_label="audio",
    )
    with wave.open(str(audio_path), "rb") as audio_file:
        audio_duration_seconds = 0.0
        # A positive sample rate places corrected rows on the consultation timeline.
        if audio_file.getframerate() > 0:
            audio_duration_seconds = audio_file.getnframes() / audio_file.getframerate()
        audio = ProductionAudioInfo(
            duration_seconds=audio_duration_seconds,
            sample_rate=audio_file.getframerate(),
            channels=audio_file.getnchannels(),
            sample_width=audio_file.getsampwidth(),
        )
        uses_browser_pcm_contract = (
            audio.sample_rate == 16_000
            and audio.channels == 1
            and audio.sample_width == 2
            and audio_file.getcomptype() == "NONE"
        )
        # A different format would exercise another path than the user's stopped recording.
        if not uses_browser_pcm_contract:
            raise ValueError(
                "invalid_audio: production shape requires 16 kHz mono 16-bit PCM WAV"
            )
        pcm_audio = audio_file.readframes(audio_file.getnframes())
    # Empty PCM cannot produce corrected wording and must stay an explicit failed case.
    if pcm_audio == b"":
        raise ValueError("invalid_audio: production WAV contains no PCM frames")
    return pcm_audio, audio, audio_sha256


def read_live_history(
    *,
    live_history_path: Path,
    expected_bytes: int,
    expected_sha256: str,
) -> tuple[dict[str, Any], str]:
    """Read the frozen rows that preserve what the clinician saw before Stop.

    Args:
        live_history_path: Explicit scaffold; absent means roles and timing cannot be preserved.
        expected_bytes: Frozen byte count; zero cannot contain a usable transcript scaffold.
        expected_sha256: Frozen digest; empty authorizes no visible-row source.

    Returns:
        Parsed non-empty history and verified digest; neither is empty on success.

    Raises:
        ValueError: The file identity or segment collection has an unsafe shape.
        json.JSONDecodeError: The copied scaffold is not valid JSON.
    """
    live_history_sha256 = verify_source_identity(
        source_path=live_history_path,
        expected_bytes=expected_bytes,
        expected_sha256=expected_sha256,
        source_label="live_history",
    )
    live_history = json.loads(live_history_path.read_text(encoding="utf-8"))
    # A non-object document cannot represent the history shown to the clinician.
    if not isinstance(live_history, dict):
        raise ValueError("source_shape: live history must be an object")
    live_segments = live_history.get("segments")
    # Missing or empty visible rows cannot preserve production timing and role scaffolding.
    if not isinstance(live_segments, list) or live_segments == []:
        raise ValueError("source_shape: live history requires transcript segments")
    # Every user-visible row must remain structured instead of being reinterpreted as raw text.
    if not all(isinstance(live_segment, dict) for live_segment in live_segments):
        raise ValueError("source_shape: every live segment must be an object")
    return live_history, live_history_sha256


def audio_metadata(
    audio: ProductionAudioInfo | None,
) -> dict[str, float | int] | None:
    """Convert validated WAV details into the operator's immutable audit row.

    Args:
        audio: Validated format details; None means the consultation was not safe to open.

    Returns:
        Serializable audio details, or None when no playable source was accepted.
    """
    # No validated audio means failure evidence must not imply that a decode started.
    if audio is None:
        return None
    return {
        "duration_seconds": audio.duration_seconds,
        "sample_rate": audio.sample_rate,
        "channels": audio.channels,
        "sample_width": audio.sample_width,
    }


def write_json_once(path: Path, payload: dict[str, Any]) -> None:
    """Create one immutable candidate artifact without replacing prior evidence.

    Args:
        path: New evidence destination; an existing file means this case already ran.
        payload: Audit data; an empty mapping still creates explicit valid JSON.

    Raises:
        FileExistsError: The operator selected a completed output from an earlier attempt.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as output_file:
        json.dump(payload, output_file, sort_keys=True, indent=2)
        output_file.write("\n")


def validate_production_options(operator_options: argparse.Namespace) -> None:
    """Reject incomplete or overwrite-prone production choices before source access.

    Args:
        operator_options: Parsed case inputs; null paths or clipping make evidence invalid.

    Raises:
        ValueError: Required sources are missing, paths are reused, or the case is not frozen.
    """
    required_source_options = (
        operator_options.live_history,
        operator_options.development_manifest,
        operator_options.fixture_stem,
        operator_options.expected_audio_bytes,
        operator_options.expected_audio_sha256,
        operator_options.expected_live_history_bytes,
        operator_options.expected_live_history_sha256,
        operator_options.expected_manifest_bytes,
        operator_options.expected_manifest_sha256,
    )
    # Missing source identity cannot bind the result to the clinician's visible transcript.
    if any(source_option is None for source_option in required_source_options):
        raise ValueError("--production-shape requires all source and identity options")
    # Missing metadata would hide why an empty transcript failed the user.
    if operator_options.metadata_output is None:
        raise ValueError("--production-shape requires --metadata-output")
    # One path cannot hold both the transcript and its separate audit metadata.
    if (
        operator_options.history_output.resolve()
        == operator_options.metadata_output.resolve()
    ):
        raise ValueError("production history and metadata outputs must differ")
    # Completed evidence is immutable and cannot become a selective replacement run.
    if (
        operator_options.history_output.exists()
        or operator_options.metadata_output.exists()
    ):
        raise ValueError("production evidence output already exists")
    # A clipped visit would no longer match the frozen full-consultation comparison.
    if operator_options.seconds is not None:
        raise ValueError("--production-shape does not permit --seconds")
    # Only one of the frozen development consultations may reach this per-case process.
    if operator_options.fixture_stem not in EXPECTED_DEVELOPMENT_STEMS:
        raise ValueError(
            "unexpected_fixture: fixture stem is not in the development corpus"
        )
    # The copied audio name keeps an operator from pairing another visit with a valid stem.
    if operator_options.audio.name != f"{operator_options.fixture_stem}.wav":
        raise ValueError("source_mismatch: audio filename does not match fixture stem")
    # The copied scaffold stays beneath its consultation name to prevent cross-case pairing.
    if operator_options.live_history.parent.name != operator_options.fixture_stem:
        raise ValueError("source_mismatch: live history does not match fixture stem")
    # A renamed manifest could represent another corpus contract than the host validated.
    if operator_options.development_manifest.name != DEVELOPMENT_MANIFEST_NAME:
        raise ValueError(f"manifest_path: expected {DEVELOPMENT_MANIFEST_NAME}")


def production_failure_category(production_error: Exception) -> str:
    """Choose a stable reason label for one retained candidate failure.

    Args:
        production_error: Failed validation or correction call; None is never passed here.

    Returns:
        Existing safe category, validation prefix, or `evaluator_failed`.
    """
    correction_reason = getattr(production_error, "reason_category", None)
    # Application correction errors already carry the support-safe reason shown to operators.
    if isinstance(correction_reason, str) and correction_reason != "":
        return correction_reason
    error_prefix = str(production_error).partition(":")[0].strip()
    # Plain validation prefixes explain pre-decode failure without exposing source content.
    if re.fullmatch(r"[a-z][a-z0-9_]*", error_prefix):
        return error_prefix
    return "evaluator_failed"


def collect_production_evidence(
    operator_options: argparse.Namespace,
    run_evidence: ProductionRunEvidence,
) -> None:
    """Validate copied sources and make at most one application correction call.

    Args:
        operator_options: Exact model, sources, and frozen identities for one case.
        run_evidence: Mutable audit state; null fields mean that work never occurred.
    """
    try:
        run_evidence.manifest_sha256_before = verify_source_identity(
            source_path=operator_options.development_manifest,
            expected_bytes=operator_options.expected_manifest_bytes,
            expected_sha256=operator_options.expected_manifest_sha256,
            source_label="development_manifest",
        )
        (
            pcm_audio,
            run_evidence.audio,
            run_evidence.audio_sha256_before,
        ) = read_production_audio(
            audio_path=operator_options.audio,
            expected_bytes=operator_options.expected_audio_bytes,
            expected_sha256=operator_options.expected_audio_sha256,
        )
        (
            run_evidence.live_history,
            run_evidence.live_history_sha256_before,
        ) = read_live_history(
            live_history_path=operator_options.live_history,
            expected_bytes=operator_options.expected_live_history_bytes,
            expected_sha256=operator_options.expected_live_history_sha256,
        )
        # A dry run proves sources and artifact wiring without importing application NeMo code.
        if operator_options.dry_run:
            run_evidence.status = "dry_run"
            return

        correction_module = importlib.import_module("post_visit_correction")
        run_evidence.module_origin = str(Path(correction_module.__file__).resolve())
        run_evidence.correction_result = correction_module.run_post_visit_correction(
            pcm_audio=pcm_audio,
            live_segments=run_evidence.live_history["segments"],
            model_name=operator_options.model,
        )
        run_evidence.status = "success"
    # Example: copied audio drifted, `/app` was absent, or correction failed after Stop.
    except Exception as caught_error:
        run_evidence.production_error = caught_error
        run_evidence.correction_result = caught_error
        run_evidence.reason_category = production_failure_category(caught_error)


def source_sha256_after_run(
    source_path: Path,
    source_sha256_before: str | None,
) -> str | None:
    """Re-hash one opened source after correction without hiding disappearance.

    Args:
        source_path: Approved input; absent after the run means source integrity failed.
        source_sha256_before: Pre-run digest; None means the source was never accepted.

    Returns:
        Post-run digest, or None when comparison is unavailable.
    """
    # A source that never passed validation has no meaningful after-run comparison.
    if source_sha256_before is None:
        return None
    try:
        return file_sha256(source_path)
    # Example: a cleanup process removed a copied source while the consultation decoded.
    except OSError:
        return None


def refresh_production_source_hashes(
    operator_options: argparse.Namespace,
    run_evidence: ProductionRunEvidence,
) -> None:
    """Record post-run identities for every source the operator authorized.

    Args:
        operator_options: Explicit paths; null source paths were rejected before this call.
        run_evidence: Before-run digests updated in place; absent digests remain explicit.
    """
    run_evidence.audio_sha256_after = source_sha256_after_run(
        operator_options.audio,
        run_evidence.audio_sha256_before,
    )
    run_evidence.live_history_sha256_after = source_sha256_after_run(
        operator_options.live_history,
        run_evidence.live_history_sha256_before,
    )
    run_evidence.manifest_sha256_after = source_sha256_after_run(
        operator_options.development_manifest,
        run_evidence.manifest_sha256_before,
    )


def is_source_pair_unchanged(
    source_sha256_before: str | None,
    source_sha256_after: str | None,
) -> bool:
    """Report whether one accepted input kept exactly the same bytes.

    Args:
        source_sha256_before: Pre-run digest; None means no source was accepted.
        source_sha256_after: Post-run digest; None means integrity could not be proved.

    Returns:
        True only when both non-empty digests match exactly.
    """
    return (
        source_sha256_before is not None and source_sha256_before == source_sha256_after
    )


def are_all_production_sources_unchanged(
    run_evidence: ProductionRunEvidence,
) -> bool:
    """Require stable audio, visible rows, and manifest before scoring wording.

    Args:
        run_evidence: Three before/after source pairs; any null value fails the gate.

    Returns:
        True only when all accepted inputs remained byte-identical.
    """
    return all(
        (
            is_source_pair_unchanged(
                run_evidence.audio_sha256_before,
                run_evidence.audio_sha256_after,
            ),
            is_source_pair_unchanged(
                run_evidence.live_history_sha256_before,
                run_evidence.live_history_sha256_after,
            ),
            is_source_pair_unchanged(
                run_evidence.manifest_sha256_before,
                run_evidence.manifest_sha256_after,
            ),
        )
    )


def production_history_for_run(
    operator_options: argparse.Namespace,
    run_evidence: ProductionRunEvidence,
) -> dict[str, Any]:
    """Expose corrected rows only after a complete source-stable application result.

    Args:
        operator_options: Selected model and source shown in evaluator provenance.
        run_evidence: Final result; missing data produces an explicit empty transcript.

    Returns:
        Scoreable corrected history, or empty history when the user would receive fallback.
    """
    has_corrected_result = (
        run_evidence.status == "success"
        and run_evidence.correction_result is not None
        and run_evidence.audio is not None
    )
    # Only a complete application correction may expose candidate words to the scorer.
    if has_corrected_result:
        source_session_id = run_evidence.live_history.get("session_id")
        # Missing source identity gets a neutral fixture label, never an invented patient ID.
        if not isinstance(source_session_id, str) or source_session_id == "":
            source_session_id = "second-pass-fixture"
        return {
            "session_id": source_session_id,
            "source": run_evidence.correction_result.source,
            "model": operator_options.model,
            "source_audio": str(operator_options.audio),
            "dry_run": False,
            "audio": audio_metadata(run_evidence.audio),
            "segments": run_evidence.correction_result.segments,
        }
    return {
        "session_id": "second-pass-fixture",
        "source": "post_visit_correction",
        "model": operator_options.model,
        "source_audio": str(operator_options.audio),
        "dry_run": operator_options.dry_run,
        "audio": audio_metadata(run_evidence.audio),
        "segments": [],
    }


def corrected_row_evidence_counts(
    corrected_segments: list[dict[str, Any]],
) -> tuple[int, int]:
    """Count visible rows carrying timing and confidence evidence for review.

    Args:
        corrected_segments: Application rows; empty means no corrected transcript was produced.

    Returns:
        Timed-row and confidence-row counts; both zero means evidence is unavailable.
    """
    timed_row_count = 0
    confidence_row_count = 0
    # Each corrected row contributes only evidence that the clinician could actually receive.
    for corrected_segment in corrected_segments:
        row_has_timing = isinstance(
            corrected_segment.get("start"), (int, float)
        ) and isinstance(corrected_segment.get("end"), (int, float))
        # Valid start/end values make this row locatable in the stopped consultation.
        if row_has_timing:
            timed_row_count += 1
        # A numeric confidence is available to the existing corrected-row presentation path.
        if isinstance(corrected_segment.get("confidence"), (int, float)):
            confidence_row_count += 1
    return timed_row_count, confidence_row_count


def production_metadata(
    operator_options: argparse.Namespace,
    run_evidence: ProductionRunEvidence,
) -> dict[str, Any]:
    """Build one audit record for production success, dry run, or failure.

    Args:
        operator_options: Explicit sources and outputs; null values failed before source access.
        run_evidence: Collected result and source hashes; missing fields remain unavailable.

    Returns:
        Serializable status, provenance, source, attempt, timing, and confidence evidence.
    """
    correction_result = run_evidence.correction_result
    corrected_segments = list(getattr(correction_result, "segments", []))
    timed_row_count, confidence_row_count = corrected_row_evidence_counts(
        corrected_segments
    )
    production_error = run_evidence.production_error
    return {
        "status": run_evidence.status,
        "reason_category": run_evidence.reason_category,
        "error_type": (
            type(production_error).__name__ if production_error is not None else None
        ),
        "error_message": str(production_error)
        if production_error is not None
        else None,
        "model": operator_options.model,
        "fixture_stem": operator_options.fixture_stem,
        "module_origin": run_evidence.module_origin,
        "audio": audio_metadata(run_evidence.audio),
        "audio_sha256_before": run_evidence.audio_sha256_before,
        "audio_sha256_after": run_evidence.audio_sha256_after,
        "audio_unchanged": is_source_pair_unchanged(
            run_evidence.audio_sha256_before,
            run_evidence.audio_sha256_after,
        ),
        "live_history_path": str(operator_options.live_history),
        "live_history_sha256_before": run_evidence.live_history_sha256_before,
        "live_history_sha256_after": run_evidence.live_history_sha256_after,
        "live_history_unchanged": is_source_pair_unchanged(
            run_evidence.live_history_sha256_before,
            run_evidence.live_history_sha256_after,
        ),
        "development_manifest_path": str(operator_options.development_manifest),
        "development_manifest_sha256_before": run_evidence.manifest_sha256_before,
        "development_manifest_sha256_after": run_evidence.manifest_sha256_after,
        "development_manifest_unchanged": is_source_pair_unchanged(
            run_evidence.manifest_sha256_before,
            run_evidence.manifest_sha256_after,
        ),
        "history_output": str(operator_options.history_output),
        "segment_count": len(corrected_segments),
        "word_count": int(getattr(correction_result, "word_count", 0)),
        "attempts": int(getattr(correction_result, "attempts", 0)),
        "retried": bool(getattr(correction_result, "retried", False)),
        "chunk_count": int(
            getattr(
                correction_result,
                "chunk_count",
                getattr(correction_result, "chunk_count_planned", 0),
            )
        ),
        "timing_state": "available" if timed_row_count > 0 else "unavailable",
        "timed_row_count": timed_row_count,
        "confidence_state": (
            "available" if confidence_row_count > 0 else "unavailable"
        ),
        "confidence_row_count": confidence_row_count,
        "dry_run": operator_options.dry_run,
    }


def run_production_shape(operator_options: argparse.Namespace) -> int:
    """Run one frozen case through the application's post-visit correction seam.

    Args:
        operator_options: Exact sources and new output paths for this consultation.

    Returns:
        Exit 0 for success/dry run, 1 for retained failure, or 2 for invalid options.
    """
    try:
        validate_production_options(operator_options)
    # Example: an operator accidentally points a fresh campaign at completed evidence.
    except ValueError as option_error:
        print(f"error: {option_error}", file=sys.stderr)
        return 2

    run_evidence = ProductionRunEvidence()
    collect_production_evidence(operator_options, run_evidence)
    refresh_production_source_hashes(operator_options, run_evidence)

    # Any source change invalidates otherwise successful wording before it reaches scoring.
    if run_evidence.status in {
        "success",
        "dry_run",
    } and not are_all_production_sources_unchanged(run_evidence):
        run_evidence.status = "failed"
        run_evidence.reason_category = "source_changed"

    history = production_history_for_run(operator_options, run_evidence)
    metadata = production_metadata(operator_options, run_evidence)
    write_json_once(operator_options.history_output, history)
    write_json_once(operator_options.metadata_output, metadata)

    # A caught failure stays visible without turning its empty score artifact into success.
    if run_evidence.production_error is not None:
        print(
            "error: production-shaped correction failed: "
            f"{type(run_evidence.production_error).__name__}: "
            f"{run_evidence.production_error}",
            file=sys.stderr,
        )
    print(
        "second-pass-asr "
        f"model={operator_options.model} "
        f"status={run_evidence.status} "
        f"segments={metadata['segment_count']} "
        f"words={metadata['word_count']} "
        f"history={operator_options.history_output}"
    )
    # A valid dry run or full correction is the only successful one-case outcome.
    if run_evidence.status in {"success", "dry_run"}:
        return 0
    return 1


def add_production_arguments(argument_parser: argparse.ArgumentParser) -> None:
    """Add the source and audit choices used only by frozen production evaluation.

    Args:
        argument_parser: Existing CLI parser; None would leave no operator command to extend.
    """
    argument_parser.add_argument(
        "--production-shape",
        action="store_true",
        help="Reuse the application's post-visit correction assembly",
    )
    argument_parser.add_argument(
        "--fixture-stem",
        default=None,
        help="Exact frozen development stem paired with this copied source",
    )
    argument_parser.add_argument(
        "--live-history",
        type=Path,
        default=None,
        help="Explicit retained visible-row scaffold for this development case",
    )
    argument_parser.add_argument(
        "--development-manifest",
        type=Path,
        default=None,
        help="Copied frozen manifest already validated by the host runner",
    )
    argument_parser.add_argument(
        "--expected-audio-bytes",
        type=int,
        default=None,
        help="Host-verified WAV byte count",
    )
    argument_parser.add_argument(
        "--expected-audio-sha256",
        default=None,
        help="Host-verified WAV SHA-256",
    )
    argument_parser.add_argument(
        "--expected-live-history-bytes",
        type=int,
        default=None,
        help="Frozen visible-row scaffold byte count",
    )
    argument_parser.add_argument(
        "--expected-live-history-sha256",
        default=None,
        help="Frozen visible-row scaffold SHA-256",
    )
    argument_parser.add_argument(
        "--expected-manifest-bytes",
        type=int,
        default=None,
        help="Host-verified development manifest byte count",
    )
    argument_parser.add_argument(
        "--expected-manifest-sha256",
        default=None,
        help="Host-verified development manifest SHA-256",
    )
