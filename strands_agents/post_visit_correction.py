"""
Post-visit transcript correction for stopped consultations.

The live UI still shows the realtime preview, but the summary path can use a
slower artifact after the user stops recording. This module runs a second ASR
pass over retained PCM audio, maps the corrected text onto the rows the user
already saw, and returns corrected rows that storage and summary citation code
can consume without changing live Mercure contracts.
"""

from __future__ import annotations

import gc
import hashlib
import logging
import os
import re
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import numpy as np
import soundfile

from corrected_role_cues import (
    CORRECTED_ROLE_FILLER_WORDS,
    DOCTOR_CONNECTOR_STARTS,
    locate_row_word_span,
    normalize_corrected_role_word,
    prepare_corrected_source_segments,
    role_from_corrected_phrase,
    split_source_row_words,
)
from nemo_confidence import (
    enable_word_confidence_decoding,
    transcript_row_confidence,
    word_confidences_for_display_words,
)
from post_visit_word_timing import (
    PostVisitTranscription,
    validated_word_confidences,
    validated_word_timings,
    wav_duration_seconds,
    word_timings_from_hypothesis,
)

logger = logging.getLogger(__name__)

DEFAULT_POST_VISIT_ASR_MODEL = os.environ.get(
    "POST_VISIT_ASR_MODEL",
    "nvidia/parakeet-unified-en-0.6b",
)
# Baseline stays inactive until the reviewed phrase proves safer transcript wording.
DEFAULT_POST_VISIT_CORRECTION_PHRASE: str | None = None
APPROVED_POST_VISIT_CORRECTION_PHRASE = "brand new sector"
_POST_VISIT_CORRECTION_PHRASE_ALPHA = 1.0
# The single-flight evaluator reads this after one decode; null means no auditable run completed.
LAST_POST_VISIT_DECODING_CONFIG: dict[str, Any] | None = None
# Normal clinician requests do not retain decoder internals; the isolated evaluator opts in.
CAPTURE_POST_VISIT_DECODING_CONFIG = False
# Keep the selected model available after the service is recreated for a later visit.
POST_VISIT_MODEL_CACHE_DIR = Path(
    os.environ.get(
        "POST_VISIT_MODEL_CACHE_DIR",
        "/data/post_visit_model_cache",
    )
)
DEFAULT_MAX_WORDS_WITHOUT_SCAFFOLD = 18
_AUDIO_SAMPLE_RATE = 16000
_MIN_ANCHOR_SCORE = 0.48
_MIN_SHORT_ANCHOR_SCORE = 0.86
_ONE_SHOT_MAX_AUDIO_SECONDS = 240.0
_AUDIO_CHUNK_SECONDS = 180.0
# A trailing remainder shorter than this rides inside the final chunk. Field
# evidence 2026-07-11: a 1.56-second tail sliver decoded empty and vetoed an
# otherwise healthy 9-minute correction, forcing the note onto live rows.
_MIN_FINAL_CHUNK_SECONDS = 10.0
_TRANSIENT_RETRY_BACKOFF_SECONDS = 2.0
_DEVICE_NOT_READY_PATTERN = re.compile(r"\bdevice\s+not\s+ready\b", re.IGNORECASE)
_CHECKPOINT_HASH_BLOCK_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class _PinnedPostVisitCheckpoint:
    """Identify one locally cached correction checkpoint exactly.

    Use this before a stopped visit loads NeMo so the clinician receives the
    checkpoint that passed evaluation, never a floating repository revision.
    """

    repository_id: str
    revision: str
    filename: str
    expected_bytes: int
    expected_sha256: str


_PINNED_POST_VISIT_CHECKPOINTS = {
    "nvidia/parakeet-unified-en-0.6b": _PinnedPostVisitCheckpoint(
        repository_id="nvidia/parakeet-unified-en-0.6b",
        revision="fe53cd885760c96b6a5f51a0bfd362cb4584a98b",
        filename="parakeet-unified-en-0.6b.nemo",
        expected_bytes=2_474_055_680,
        expected_sha256=(
            "ec23ed9150c8fde49072c3e2d61678ab903dbcef389d658db833420cbc1da35b"
        ),
    ),
}


class PostVisitCorrectionError(RuntimeError):
    """
    Raised when the correction pass cannot create user-visible rows.

    Use this for recoverable post-stop failures: the browser can still
    summarize the live preview, but the user should not be told that a
    corrected transcript exists.
    """

    def __init__(
        self,
        message: str,
        *,
        attempts: int = 0,
        retried: bool = False,
        reason_category: str = "correction_failed",
        failed_chunk_index: int | None = None,
        chunk_count_planned: int = 0,
    ) -> None:
        """Keep safe recovery metadata with one unavailable correction.

        Use when the browser must fall back to live rows without seeing raw
        CUDA details. Zero attempts means ASR never started; a null failed
        chunk means the request failed before an ordered audio piece ran.

        Args:
            message: Internal diagnostic text; callers must not return it to the browser.
            attempts: Model calls made for the failed chunk; zero means ASR never started.
            retried: True when the failed chunk consumed the one recovery retry.
            reason_category: PHI-safe failure label for browser/support provenance.
            failed_chunk_index: One-based failed audio piece; null means no chunk ran.
            chunk_count_planned: Total ordered pieces planned; zero means none were built.
        """
        super().__init__(message)
        self.attempts = attempts
        self.retried = retried
        self.reason_category = reason_category
        self.failed_chunk_index = failed_chunk_index
        self.chunk_count_planned = chunk_count_planned


@dataclass(frozen=True, slots=True)
class PostVisitCorrectionResult:
    """
    Corrected transcript rows and metadata for one stopped session.

    The FastAPI endpoint stores `segments` in corrected transcript storage and
    returns the metadata to the browser/dev panel. Empty segments mean the
    caller should fall back to the live preview transcript.

    Attributes:
        segments: Corrected rows the summary cites; empty means the user keeps the live preview.
        model_name: ASR model shown in debug/provenance; empty is not emitted by this result.
        word_count: Corrected ASR word total; zero means no useful transcript was created.
        source: Correction lane label used by API responses and summary evidence.
        attempts: Transcribe attempt count; `2` means one allowlisted retry occurred.
        retried: True only when the user waited for that one recovery retry.
        chunk_count: Audio pieces transcribed in order; `1` is the unchanged short-visit path.
    """

    segments: list[dict[str, Any]]
    model_name: str
    word_count: int
    source: str = "post_visit_correction"
    attempts: int = 1
    retried: bool = False
    chunk_count: int = 1


@dataclass(frozen=True, slots=True)
class AnchorMatch:
    """
    Match between one live preview row and the second-pass ASR word stream.

    The corrected transcript uses this to keep the user's row timing and role
    context while anchoring words to the preview text they likely replace. A
    missing match means the safer proportional fallback should be used.

    Attributes:
        start_index: First corrected ASR word for this visible row.
        end_index: One-past-last corrected ASR word; equal to start means no visible text.
        score: Match confidence; low scores fall back to row-share allocation.
    """

    start_index: int
    end_index: int
    score: float


@dataclass(frozen=True, slots=True)
class _NemoTranscriptionResult:
    """Carry recombined NeMo evidence and recovery metadata into correction.

    The user sees its text as corrected rows; attempt and chunk fields explain
    whether the request used bounded audio or a transient retry. Missing timing
    or confidence keeps the existing unstyled, unsplit rendering.

    Attributes:
        effective_decoding_config: Evaluator-only decoder evidence; null means the clinician used the
            normal path or no auditable decode completed.
    """

    text: str
    word_timings: list[dict[str, Any]] | None
    word_confidences: list[float] | None
    attempts: int
    retried: bool
    chunk_count: int
    effective_decoding_config: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class _AudioChunk:
    """Describe one ordered WAV submitted to the restored correction model.

    Short visits point at the original retained-audio WAV and keep it. Long
    visits point at bounded scratch WAVs whose offset restores visit-relative
    timing and whose cleanup flag prevents temporary-file leaks.
    """

    path: Path
    start_seconds: float
    delete_after_use: bool


@dataclass(frozen=True, slots=True)
class _TranscribeCallResult:
    """Keep one successful model result and its retry outcome together.

    Use after a chunk or short visit transcribes so the correction response can
    distinguish a normal call from the single user-visible recovery attempt.
    """

    hypotheses: list[Any]
    attempts: int
    retried: bool


def run_post_visit_correction(
    *,
    pcm_audio: bytes,
    live_segments: list[dict[str, Any]],
    model_name: str = DEFAULT_POST_VISIT_ASR_MODEL,
    transcribe_audio_file: Callable[
        [str, str], PostVisitTranscription | _NemoTranscriptionResult | str
    ]
    | None = None,
) -> PostVisitCorrectionResult:
    """Run second-pass ASR and map corrected text onto stored live rows.

    Args:
        pcm_audio: Retained 16 kHz mono PCM from the stopped browser session; empty cannot be corrected.
        live_segments: Current transcript rows used as timing/role scaffolding; empty produces generic rows.
        model_name: ASR model id shown in corrected-row provenance; empty uses the configured default.
        transcribe_audio_file: Optional test seam returning rich or plain-text output; null loads
            the configured NeMo ASR model.

    Returns:
        Corrected rows plus model metadata; empty ASR text raises a correction error.

    Raises:
        PostVisitCorrectionError: When no corrected artifact can be shown after Stop.
    """
    # No retained audio means the user stopped too late for a post-visit ASR pass.
    if pcm_audio == b"":
        raise PostVisitCorrectionError("No retained audio is available for correction.")

    # No model override was provided, so the user gets the configured correction model.
    selected_model_name = model_name or DEFAULT_POST_VISIT_ASR_MODEL
    audio_path = _write_pcm_wav(pcm_audio)
    try:
        # Tests can inject a transcriber; the real UI path uses the configured NeMo model.
        transcriber = transcribe_audio_file or transcribe_audio_with_nemo
        raw_transcription = transcriber(selected_model_name, str(audio_path))
        transcript_text, raw_word_timings, raw_word_confidences = (
            coerce_post_visit_transcription(raw_transcription)
        )
    finally:
        audio_path.unlink(missing_ok=True)

    words = split_words(transcript_text)
    # ASR returning no words means corrected storage would only hide useful live text.
    if not words:
        raise PostVisitCorrectionError("Second-pass ASR returned no transcript text.")

    corrected_segments = build_corrected_segments(
        corrected_words=words,
        live_segments=live_segments,
        model_name=selected_model_name,
        word_timings=validated_word_timings(raw_word_timings, words),
        word_confidences=validated_word_confidences(raw_word_confidences, words),
    )
    # Empty corrected rows mean alignment had no user-visible artifact to store.
    if corrected_segments == []:
        raise PostVisitCorrectionError("Second-pass ASR produced no corrected rows.")

    logger.info(
        "post_visit_correction.completed",
        extra={
            "segments": len(corrected_segments),
            "words": len(words),
            "model": selected_model_name,
            "attempts": int(getattr(raw_transcription, "attempts", 1)),
            "retried": bool(getattr(raw_transcription, "retried", False)),
            "chunk_count": int(getattr(raw_transcription, "chunk_count", 1)),
        },
    )

    return PostVisitCorrectionResult(
        segments=corrected_segments,
        model_name=selected_model_name,
        word_count=len(words),
        attempts=int(getattr(raw_transcription, "attempts", 1)),
        retried=bool(getattr(raw_transcription, "retried", False)),
        chunk_count=int(getattr(raw_transcription, "chunk_count", 1)),
    )


def _apply_post_visit_correction_phrase(
    asr_model: Any,
    correction_phrase: str | None,
) -> None:
    """Apply one reviewed phrase after confidence setup for a stopped visit.

    Use only in the corrected transcript lane; null keeps the current words unchanged.

    Args:
        asr_model: Restored Unified model; missing decoder config stops correction before visible wording.
        correction_phrase: Independently reviewed official wording; null preserves the baseline transcript.

    Returns:
        None; the model changes in place, while a null phrase leaves its decoder untouched.

    Raises:
        PostVisitCorrectionError: When the phrase or decoder is outside the approved experiment.
    """
    # No reviewed phrase means the clinician receives the unchanged baseline decode.
    if correction_phrase is None:
        return
    # Any other text could normalize a raw garble or activate multiple unreviewed hints.
    if correction_phrase != APPROVED_POST_VISIT_CORRECTION_PHRASE:
        raise PostVisitCorrectionError(
            "Post-visit correction phrase is not the reviewed canonical phrase.",
            reason_category="invalid_phrase_config",
        )

    from omegaconf import OmegaConf, open_dict

    phrase_decoding_config = OmegaConf.create(
        OmegaConf.to_container(asr_model.cfg.decoding, resolve=True)
    )
    # Native phrase fusion is proven only for the checkpoint's batched greedy decoder.
    if phrase_decoding_config.strategy != "greedy_batch":
        raise PostVisitCorrectionError(
            "Post-visit correction phrase requires greedy_batch decoding.",
            reason_category="unsupported_phrase_config",
        )

    with open_dict(phrase_decoding_config.greedy):
        phrase_decoding_config.greedy.boosting_tree = {
            "key_phrases_list": [correction_phrase]
        }
        phrase_decoding_config.greedy.boosting_tree_alpha = (
            _POST_VISIT_CORRECTION_PHRASE_ALPHA
        )
    asr_model.change_decoding_strategy(phrase_decoding_config)


def _effective_post_visit_decoding_config(asr_model: Any) -> dict[str, Any]:
    """Capture the decoder settings that produced the corrected transcript.

    Use after decoding so reviewers can verify phrase, confidence, and timestamps together.

    Args:
        asr_model: Model that produced the stopped visit; missing config cannot support an accuracy claim.

    Returns:
        Plain decoder settings for evidence; an empty mapping is valid if NeMo reports one.

    Raises:
        PostVisitCorrectionError: When NeMo does not expose an auditable mapping.
    """
    from omegaconf import OmegaConf

    effective_decoding_config = OmegaConf.to_container(
        asr_model.cfg.decoding,
        resolve=True,
    )
    # A non-object config cannot prove which decoder settings produced the visible words.
    if not isinstance(effective_decoding_config, dict):
        raise PostVisitCorrectionError(
            "Post-visit decoding configuration is not auditable.",
            reason_category="invalid_decoding_evidence",
        )
    return effective_decoding_config


def _selected_post_visit_correction_phrase(
    requested_correction_phrase: str | None,
) -> str | None:
    """Choose the reviewed phrase for one stopped-visit decode.

    Use for normal and evaluator calls; null means the clinician receives the internal default.

    Args:
        requested_correction_phrase: Evaluator override; null uses the inactive production default.

    Returns:
        Reviewed phrase to apply, or null when the baseline transcript stays unchanged.
    """
    # No evaluator override means the clinician receives the current internal default.
    if requested_correction_phrase is None:
        return DEFAULT_POST_VISIT_CORRECTION_PHRASE
    return requested_correction_phrase


def _capture_post_visit_decoding_evidence(
    asr_model: Any,
) -> dict[str, Any] | None:
    """Retain decoder evidence only for the isolated accuracy evaluator.

    Use after a completed A/B decode; normal clinician requests return null and retain no new state.

    Args:
        asr_model: Model that produced the transcript; missing config makes evaluator evidence unavailable.

    Returns:
        Effective decoder mapping for an evaluator run, or null for the normal user path.

    Raises:
        PostVisitCorrectionError: When an opted-in evaluator cannot read decoder evidence.
    """
    # Normal clinician requests keep decoder internals out of application state.
    if not CAPTURE_POST_VISIT_DECODING_CONFIG:
        return None

    effective_decoding_config = _effective_post_visit_decoding_config(asr_model)
    global LAST_POST_VISIT_DECODING_CONFIG
    LAST_POST_VISIT_DECODING_CONFIG = effective_decoding_config
    return effective_decoding_config


def transcribe_audio_with_nemo(
    model_name: str,
    audio_path: str,
    correction_phrase: str | None = None,
) -> _NemoTranscriptionResult:
    """Transcribe retained visit audio on one restored NeMo model.

    Short visits keep the original one-shot call. Capacity-risk visits use
    sequential chunks, and only the observed device-not-ready family receives
    one same-model retry before the browser falls back to live rows.

    Args:
        model_name: NVIDIA/NeMo model id; empty would fail model loading.
        audio_path: WAV path written from the stopped browser audio buffer.
        correction_phrase: Reviewed phrase for this offline pass; null uses the
            inactive production default and preserves baseline wording.

    Returns:
        Recombined transcript evidence plus attempts/chunk metadata; empty text
        means correction cannot replace the user's live transcript.

    Raises:
        PostVisitCorrectionError: When NeMo cannot load or transcribe the stopped visit audio.
    """
    _release_cached_cuda_memory_before_model_restore()
    asr_model = _load_post_visit_asr_model(model_name)
    # Confidence is a pure observer of the decode and never changes visible words.
    try:
        enable_word_confidence_decoding(asr_model)
    except Exception as confidence_error:  # pragma: no cover - NeMo-version-specific.
        # Example: the user requests a note with an older override model that lacks this config.
        logger.warning(
            "post_visit_correction.confidence_enable_failed %s",
            type(confidence_error).__name__,
        )

    selected_correction_phrase = _selected_post_visit_correction_phrase(
        correction_phrase
    )
    _apply_post_visit_correction_phrase(asr_model, selected_correction_phrase)

    audio_chunks = _build_audio_chunks(Path(audio_path))
    combined_text_parts: list[str] = []
    combined_word_timings: list[dict[str, Any]] = []
    combined_word_confidences: list[float] = []
    all_chunks_have_timings = True
    all_chunks_have_confidence = True
    attempts = 1
    retried = False
    try:
        # Each ordered chunk reuses the same model so only activation memory is bounded.
        for failed_chunk_index, audio_chunk in enumerate(audio_chunks, start=1):
            try:
                transcribe_call = _transcribe_with_loaded_model(
                    asr_model,
                    str(audio_chunk.path),
                )
            except PostVisitCorrectionError as correction_error:
                # Support needs the exact failed piece without receiving audio or CUDA prose.
                raise PostVisitCorrectionError(
                    str(correction_error),
                    attempts=correction_error.attempts,
                    retried=correction_error.retried,
                    reason_category=correction_error.reason_category,
                    failed_chunk_index=failed_chunk_index,
                    chunk_count_planned=len(audio_chunks),
                ) from correction_error
            attempts = max(attempts, transcribe_call.attempts)
            retried = retried or transcribe_call.retried
            # An empty chunk would silently remove part of the user's consultation.
            if transcribe_call.hypotheses == []:
                raise PostVisitCorrectionError(
                    "Second-pass ASR returned no transcript for one audio chunk.",
                    attempts=attempts,
                    retried=retried,
                    reason_category="empty_result",
                    failed_chunk_index=failed_chunk_index,
                    chunk_count_planned=len(audio_chunks),
                )

            chunk_transcription = _transcription_from_hypothesis(
                transcribe_call.hypotheses[0],
                str(audio_chunk.path),
                audio_chunk.start_seconds,
            )
            # Empty decoded text is not a complete corrected source for the note.
            if chunk_transcription.text == "":
                raise PostVisitCorrectionError(
                    "Second-pass ASR returned no transcript text for one audio chunk.",
                    attempts=attempts,
                    retried=retried,
                    reason_category="empty_result",
                    failed_chunk_index=failed_chunk_index,
                    chunk_count_planned=len(audio_chunks),
                )
            combined_text_parts.append(chunk_transcription.text)
            # Missing timing on one chunk makes the combined timing stream incomplete.
            if chunk_transcription.word_timings is None:
                all_chunks_have_timings = False
            else:
                combined_word_timings.extend(chunk_transcription.word_timings)
            # Missing confidence on one chunk keeps all recombined rows unmeasured.
            if chunk_transcription.word_confidences is None:
                all_chunks_have_confidence = False
            else:
                combined_word_confidences.extend(chunk_transcription.word_confidences)
    finally:
        _remove_scratch_audio_chunks(audio_chunks)

    effective_decoding_config = _capture_post_visit_decoding_evidence(asr_model)
    return _NemoTranscriptionResult(
        text=" ".join(combined_text_parts),
        word_timings=combined_word_timings if all_chunks_have_timings else None,
        word_confidences=(
            combined_word_confidences if all_chunks_have_confidence else None
        ),
        attempts=attempts,
        retried=retried,
        chunk_count=len(audio_chunks),
        effective_decoding_config=effective_decoding_config,
    )


def _release_cached_cuda_memory_before_model_restore() -> None:
    """Release unreachable correction allocations before restoring the model.

    Use after a user finishes streaming or a prior correction: active live
    models remain allocated, while stale cache cannot starve this note request.
    """
    gc.collect()
    try:
        import torch
    except (
        Exception
    ) as torch_import_error:  # pragma: no cover - NeMo image always has torch.
        # Example: an unusual agent image reaches Summarise without its GPU runtime import.
        logger.warning(
            "post_visit_correction.preload_cache_release_import_failed %s",
            type(torch_import_error).__name__,
        )
        return

    try:
        torch.cuda.empty_cache()
    except Exception as cache_release_error:  # pragma: no cover - GPU-state-specific.
        # Example: a prior visit left the GPU unavailable before this user's model can load.
        logger.warning(
            "post_visit_correction.preload_cache_release_failed %s",
            type(cache_release_error).__name__,
        )


def _prepare_loaded_post_visit_asr_model(
    asr_model: Any,
    model_name: str,
) -> Any:
    """Give exact Unified the empty loader config it needs for transcription.

    Use after restore so a stopped visit can reach NeMo decoding without changing
    transcript settings or any other selected model.

    Args:
        asr_model: Restored speech model; missing config means no corrected transcript.
        model_name: Operator-selected model; empty or another ID stays unchanged.

    Returns:
        The same ready model; this function never returns null to the user flow.
    """
    # Other selected models retain their checkpoint-defined transcription setup.
    if model_name != "nvidia/parakeet-unified-en-0.6b":
        return asr_model

    unified_model_config = getattr(asr_model, "cfg", None)
    # Missing Unified config cannot produce a safe corrected transcript.
    if unified_model_config is None:
        raise PostVisitCorrectionError(
            "Unified post-visit ASR has no model configuration.",
            reason_category="model_load_failed",
        )

    unified_validation_config = getattr(
        unified_model_config,
        "validation_ds",
        None,
    )
    # A checkpoint-supplied validation config already supports its transcript loader.
    if unified_validation_config is not None:
        return asr_model

    from omegaconf import open_dict

    # The empty config selects NeMo's existing false default without tuning decoding.
    with open_dict(unified_model_config):
        unified_model_config.validation_ds = {}

    return asr_model


def _load_post_visit_asr_model(model_name: str) -> Any:
    """Restore the configured correction model once for one user request.

    Use before short or chunked transcription; a failure means no GPU attempt
    occurred and the browser must continue from the live transcript.
    """
    try:
        import nemo.collections.asr as nemo_asr
    except Exception as import_error:  # pragma: no cover - depends on NeMo container.
        # Example: the user requests a summary while the agent image lacks NeMo imports.
        raise PostVisitCorrectionError(
            "NeMo ASR is unavailable for post-visit correction.",
            reason_category="model_unavailable",
        ) from import_error

    try:
        verified_checkpoint_path = _verified_checkpoint_path(model_name)
        # A pinned model restores the exact artifact that earned clinician use.
        if verified_checkpoint_path is not None:
            restored_post_visit_model = nemo_asr.models.ASRModel.restore_from(
                restore_path=str(verified_checkpoint_path)
            )
            return _prepare_loaded_post_visit_asr_model(
                restored_post_visit_model, model_name
            )

        return nemo_asr.models.ASRModel.from_pretrained(model_name=model_name)
    except PostVisitCorrectionError:
        raise
    except Exception as model_load_error:  # pragma: no cover - GPU/model-specific.
        # Example: the clinician clicks Summarise while the configured checkpoint cannot load.
        raise PostVisitCorrectionError(
            f"Second-pass ASR model failed to load: {type(model_load_error).__name__}",
            reason_category="model_load_failed",
        ) from model_load_error


def _checkpoint_sha256(checkpoint_path: Path) -> str:
    """Hash one local NeMo artifact in bounded blocks.

    Use before loading a stopped visit so cache drift cannot silently change
    the transcript model behind the existing clinician workflow.
    """
    checkpoint_hash = hashlib.sha256()
    with checkpoint_path.open("rb") as checkpoint_file:
        # Bounded reads verify the large model without exhausting app memory.
        while checkpoint_block := checkpoint_file.read(_CHECKPOINT_HASH_BLOCK_BYTES):
            checkpoint_hash.update(checkpoint_block)
    return checkpoint_hash.hexdigest()


def _verified_checkpoint_path(model_name: str) -> Path | None:
    """Resolve and verify a known correction checkpoint from local cache.

    Use before NeMo restore; null means an explicit operator override keeps
    the existing repository-ID loading behavior for the stopped visit.
    """
    pinned_checkpoint = _PINNED_POST_VISIT_CHECKPOINTS.get(model_name)
    # An unknown explicit override retains the pre-existing operator seam.
    if pinned_checkpoint is None:
        return None

    try:
        from huggingface_hub import hf_hub_download

        checkpoint_path = Path(
            hf_hub_download(
                repo_id=pinned_checkpoint.repository_id,
                revision=pinned_checkpoint.revision,
                filename=pinned_checkpoint.filename,
                cache_dir=str(POST_VISIT_MODEL_CACHE_DIR),
                local_files_only=True,
            )
        ).resolve(strict=True)
    except Exception as checkpoint_cache_error:
        # Example: the clinician stops a visit after the pinned cache file was removed.
        raise PostVisitCorrectionError(
            "Pinned post-visit ASR checkpoint is unavailable from local cache.",
            reason_category="model_load_failed",
        ) from checkpoint_cache_error

    # A directory or broken cache target cannot produce the evaluated transcript.
    if not checkpoint_path.is_file():
        raise PostVisitCorrectionError(
            "Pinned post-visit ASR checkpoint is not a file.",
            reason_category="model_load_failed",
        )

    try:
        checkpoint_bytes = checkpoint_path.stat().st_size
        checkpoint_sha256 = _checkpoint_sha256(checkpoint_path)
    except OSError as checkpoint_read_error:
        # Example: cache permissions change while the user's stopped visit is awaiting correction.
        raise PostVisitCorrectionError(
            "Pinned post-visit ASR checkpoint could not be verified.",
            reason_category="model_load_failed",
        ) from checkpoint_read_error

    # A byte-size mismatch means this is not the frozen candidate or baseline.
    if checkpoint_bytes != pinned_checkpoint.expected_bytes:
        raise PostVisitCorrectionError(
            "Pinned post-visit ASR checkpoint size does not match.",
            reason_category="model_load_failed",
        )

    # A hash mismatch blocks a plausible but different model from reaching the note.
    if checkpoint_sha256 != pinned_checkpoint.expected_sha256:
        raise PostVisitCorrectionError(
            "Pinned post-visit ASR checkpoint hash does not match.",
            reason_category="model_load_failed",
        )

    return checkpoint_path


def _build_audio_chunks(audio_path: Path) -> list[_AudioChunk]:
    """Keep short audio whole or split a capacity-risk visit into bounded WAVs.

    Use after Stop: offsets let recombined word timings still point at the
    original consultation. Returned scratch chunks are owned by the caller.
    A trailing remainder under `_MIN_FINAL_CHUNK_SECONDS` joins the final
    chunk so a silence-length sliver cannot void the corrected transcript.
    """
    audio_duration_seconds = wav_duration_seconds(str(audio_path))
    # The measured short-visit envelope preserves the original one-shot call exactly.
    if audio_duration_seconds <= _ONE_SHOT_MAX_AUDIO_SECONDS:
        return [_AudioChunk(audio_path, 0.0, False)]

    scratch_chunks: list[_AudioChunk] = []
    try:
        audio_samples, sample_rate = soundfile.read(
            audio_path,
            dtype="float32",
            always_2d=False,
        )
        # Browser-retained correction audio is mono; another shape is not safe to recombine.
        if not isinstance(audio_samples, np.ndarray) or audio_samples.ndim != 1:
            raise PostVisitCorrectionError(
                "Retained correction audio is not mono.",
                reason_category="invalid_audio",
            )
        chunk_sample_count = max(1, int(float(sample_rate) * _AUDIO_CHUNK_SECONDS))
        min_final_sample_count = int(float(sample_rate) * _MIN_FINAL_CHUNK_SECONDS)
        slice_starts = list(range(0, len(audio_samples), chunk_sample_count))
        # A near-silent tail sliver must not become its own failable chunk and
        # cost the user an otherwise healthy corrected note.
        if (
            len(slice_starts) > 1
            and len(audio_samples) - slice_starts[-1] < min_final_sample_count
        ):
            slice_starts.pop()
        # Each fixed-size slice bounds model activation memory and keeps spoken order.
        for slice_index, start_sample in enumerate(slice_starts):
            # The final slice absorbs any sub-threshold remainder of the visit.
            is_final_slice = slice_index == len(slice_starts) - 1
            end_sample = (
                len(audio_samples)
                if is_final_slice
                else min(len(audio_samples), start_sample + chunk_sample_count)
            )
            with tempfile.NamedTemporaryFile(
                suffix=".wav",
                prefix="post_visit_correction_chunk_",
                delete=False,
            ) as scratch_file:
                chunk_path = Path(scratch_file.name)
            soundfile.write(
                chunk_path, audio_samples[start_sample:end_sample], sample_rate
            )
            scratch_chunks.append(
                _AudioChunk(
                    path=chunk_path,
                    start_seconds=start_sample / float(sample_rate),
                    delete_after_use=True,
                )
            )
    except PostVisitCorrectionError:
        _remove_scratch_audio_chunks(scratch_chunks)
        raise
    except Exception as chunk_error:
        # Example: a stopped visit cannot create its temporary bounded WAVs on disk.
        _remove_scratch_audio_chunks(scratch_chunks)
        raise PostVisitCorrectionError(
            f"Could not prepare retained audio for correction: {type(chunk_error).__name__}",
            reason_category="audio_preparation_failed",
        ) from chunk_error

    return scratch_chunks


def _remove_scratch_audio_chunks(audio_chunks: list[_AudioChunk]) -> None:
    """Delete only the bounded scratch WAVs created for a long visit.

    Use after success or failure; the original short-visit WAV remains owned by
    `run_post_visit_correction` and is removed by its existing finally block.
    """
    # Every owned chunk must disappear so repeated summaries do not fill local storage.
    for audio_chunk in audio_chunks:
        # The original retained-audio WAV has a separate owner and must remain here.
        if not audio_chunk.delete_after_use:
            continue
        audio_chunk.path.unlink(missing_ok=True)


def _transcribe_with_loaded_model(
    asr_model: Any, audio_path: str
) -> _TranscribeCallResult:
    """Run one audio path with at most one allowlisted same-model retry.

    Use for the original short WAV or each long-visit chunk. Fatal failures
    return immediately so the user is not kept waiting on a hopeless retry.
    """
    try:
        hypotheses = _transcribe_loaded_model_once(asr_model, audio_path)
    except Exception as first_transcribe_error:
        # Only the exact field-observed device-not-ready family earns another wait.
        if not _is_device_not_ready_error(first_transcribe_error):
            raise PostVisitCorrectionError(
                (
                    "Second-pass ASR failed: "
                    f"{type(first_transcribe_error).__name__}: {first_transcribe_error}"
                ),
                attempts=1,
                retried=False,
                reason_category=_transcribe_failure_category(first_transcribe_error),
            ) from first_transcribe_error

        _reclaim_cuda_memory()
        time.sleep(_TRANSIENT_RETRY_BACKOFF_SECONDS)
        try:
            hypotheses = _transcribe_loaded_model_once(asr_model, audio_path)
        except Exception as second_transcribe_error:
            # Example: the clinician waited for recovery, but the same GPU call failed again.
            raise PostVisitCorrectionError(
                (
                    "Second-pass ASR failed after one retry: "
                    f"{type(second_transcribe_error).__name__}: {second_transcribe_error}"
                ),
                attempts=2,
                retried=True,
                reason_category=_transcribe_failure_category(second_transcribe_error),
            ) from second_transcribe_error

        return _TranscribeCallResult(hypotheses, attempts=2, retried=True)

    return _TranscribeCallResult(hypotheses, attempts=1, retried=False)


def _transcribe_loaded_model_once(asr_model: Any, audio_path: str) -> list[Any]:
    """Call the restored model once while preserving timestamp compatibility.

    Use inside the retry wrapper; older override models fall back to text-only
    decoding without counting that signature adjustment as another attempt.
    """
    try:
        return asr_model.transcribe(
            [audio_path],
            return_hypotheses=True,
            timestamps=True,
        )
    except TypeError:
        # Example: a local override model predates the timestamp option used by the note UI.
        return asr_model.transcribe([audio_path], return_hypotheses=True)


def _is_device_not_ready_error(transcribe_error: Exception) -> bool:
    """Recognize only the observed transient across an exception/cause chain.

    Use before making the user wait for a retry; all other CUDA text is fatal.
    """
    return (
        _DEVICE_NOT_READY_PATTERN.search(_exception_chain_text(transcribe_error))
        is not None
    )


def _exception_chain_text(error: BaseException) -> str:
    """Join safe exception type/message text for retry classification.

    Use for GPU error signatures only; this text is never returned to the browser.
    """
    chain_parts: list[str] = []
    seen_errors: set[int] = set()
    current_error: BaseException | None = error
    # Each unseen cause/context may carry the CUDA signature hidden by a wrapper.
    while current_error is not None and id(current_error) not in seen_errors:
        seen_errors.add(id(current_error))
        chain_parts.append(f"{type(current_error).__name__}: {current_error}")
        current_error = current_error.__cause__ or current_error.__context__

    return " | ".join(chain_parts)


def _transcribe_failure_category(transcribe_error: Exception) -> str:
    """Map a model failure to a sanitized browser/support category.

    Use when correction becomes unavailable; categories contain no clinical or
    raw CUDA prose and let the UI show one neutral fallback explanation.
    """
    error_text = _exception_chain_text(transcribe_error).casefold()
    # The one allowlisted transient exhausted its retry.
    if _DEVICE_NOT_READY_PATTERN.search(error_text) is not None:
        return "gpu_transient"
    # OOM means this request exceeded available correction capacity.
    if "out of memory" in error_text or "cuda oom" in error_text:
        return "gpu_capacity"
    # Corrupted-device failures are fatal and must never be retried in-process.
    if "illegal memory access" in error_text or "device-side assert" in error_text:
        return "gpu_fatal"

    return "transcribe_failed"


def _reclaim_cuda_memory() -> None:
    """Best-effort synchronize and release cached allocations before one retry.

    Use only after device-not-ready. Failure here remains non-fatal because the
    user still receives the promised second model call and then a live fallback.
    """
    try:
        import torch
    except (
        Exception
    ) as torch_import_error:  # pragma: no cover - NeMo image always has torch.
        # Example: an unusual agent image cannot prepare the GPU before the user's retry.
        logger.warning(
            "post_visit_correction.cuda_reclaim_import_failed %s",
            type(torch_import_error).__name__,
        )
        return

    try:
        torch.cuda.synchronize()
    except Exception as synchronize_error:  # pragma: no cover - GPU-state-specific.
        # Example: the user's first failed kernel leaves synchronization unavailable.
        logger.warning(
            "post_visit_correction.cuda_synchronize_failed %s",
            type(synchronize_error).__name__,
        )
    try:
        torch.cuda.empty_cache()
    except Exception as cache_error:  # pragma: no cover - GPU-state-specific.
        # Example: cached allocations cannot be released before the user's one retry.
        logger.warning(
            "post_visit_correction.cuda_empty_cache_failed %s",
            type(cache_error).__name__,
        )


def _transcription_from_hypothesis(
    hypothesis: Any,
    audio_path: str,
    start_seconds: float,
) -> PostVisitTranscription:
    """Extract one chunk's text and shift evidence onto the full visit timeline.

    Use after a successful model call; absent timing/confidence remains honest
    optional evidence and never blocks the corrected note by itself.
    """
    transcript_text = normalise_transcript_text(hypothesis)
    word_timings: list[dict[str, Any]] | None = None
    try:
        chunk_word_timings = word_timings_from_hypothesis(
            hypothesis,
            wav_duration_seconds(audio_path),
        )
        # Each timing row moves from chunk-relative to consultation-relative seconds.
        if chunk_word_timings:
            word_timings = [
                {
                    **timing,
                    "start": float(timing["start"]) + start_seconds,
                    "end": float(timing["end"]) + start_seconds,
                }
                for timing in chunk_word_timings
            ]
    except Exception as timing_error:  # pragma: no cover - NeMo-hypothesis-specific.
        # Example: the user gets corrected text even when this model omits usable word times.
        logger.warning(
            "post_visit_correction.word_timing_extraction_failed %s",
            type(timing_error).__name__,
        )

    word_confidences: list[float] | None = None
    try:
        word_confidences = word_confidences_for_display_words(
            hypothesis,
            split_words(transcript_text),
        )
    except Exception as confidence_error:  # pragma: no cover - hypothesis-specific.
        # Example: the corrected note renders without confidence styling for this model.
        logger.warning(
            "post_visit_correction.word_confidence_extraction_failed %s",
            type(confidence_error).__name__,
        )

    return PostVisitTranscription(
        text=transcript_text,
        word_timings=word_timings,
        word_confidences=word_confidences,
    )


def coerce_post_visit_transcription(
    transcription: PostVisitTranscription | _NemoTranscriptionResult | str,
) -> tuple[str, list[dict[str, Any]] | None, list[float] | None]:
    """Normalise rich or plain transcriber output into text plus optional evidence.

    Args:
        transcription: Transcriber return value; plain strings come from timing-unaware seams.

    Returns:
        `(text, word_timings, word_confidences)`; None timings mean no timing-based
        split can run, None confidences leave corrected rows unmeasured.
    """
    # Evidence-aware transcribers return the richer shape with timing and confidence.
    if isinstance(transcription, (PostVisitTranscription, _NemoTranscriptionResult)):
        return (
            transcription.text,
            transcription.word_timings,
            transcription.word_confidences,
        )

    return normalise_transcript_text(transcription), None, None


def _write_pcm_wav(pcm_audio: bytes) -> Path:
    """Write retained browser PCM to a temporary WAV for NeMo ASR.

    Args:
        pcm_audio: 16-bit PCM bytes; empty is rejected before this helper.

    Returns:
        Temporary WAV path; caller owns deletion after ASR finishes.
    """
    pcm_samples = np.frombuffer(pcm_audio, dtype=np.int16).astype(np.float32) / 32768.0
    with tempfile.NamedTemporaryFile(
        suffix=".wav",
        prefix="post_visit_correction_",
        delete=False,
    ) as scratch_file:
        audio_path = Path(scratch_file.name)

    soundfile.write(audio_path, pcm_samples, _AUDIO_SAMPLE_RATE)
    return audio_path


def normalise_transcript_text(value: Any) -> str:
    """Extract plain text from the variety of NeMo ASR return shapes.

    Args:
        value: ASR result object; null means no text could be decoded.

    Returns:
        Trimmed transcript text; empty means no corrected row should be stored.
    """
    # Null ASR output means there is no corrected transcript for the user.
    if value is None:
        return ""

    text = getattr(value, "text", None)
    # NeMo hypothesis objects expose `.text`; dictionaries may appear in tests.
    if text is not None:
        return str(text).strip()

    # Some mocked or future outputs may use a plain dictionary shape.
    if isinstance(value, dict) and "text" in value:
        return str(value["text"]).strip()

    return str(value).strip()


def split_words(text: str) -> list[str]:
    """Return display words while preserving punctuation attached to words.

    Args:
        text: ASR transcript text; empty means no corrected rows can be created.

    Returns:
        Word list in spoken order; empty means ASR produced no useful text.
    """
    return re.findall(r"\S+", text.strip())


def build_corrected_segments(
    *,
    corrected_words: list[str],
    live_segments: list[dict[str, Any]],
    model_name: str,
    word_timings: list[dict[str, Any]] | None = None,
    word_confidences: list[float] | None = None,
) -> list[dict[str, Any]]:
    """Map second-pass words onto live rows for corrected summary input.

    Args:
        corrected_words: ASR tokens in spoken order; empty means no corrected rows.
        live_segments: Stored or browser-visible preview rows; empty creates generic chunks.
        model_name: ASR model id recorded on every corrected row.
        word_timings: Validated per-word timings aligned to `corrected_words`; None
            means echo-boundary rows stay whole.
        word_confidences: Validated per-word confidence aligned to `corrected_words`;
            None leaves every corrected row unmeasured.

    Returns:
        Corrected segment rows in chronological order; empty means no artifact exists.
    """
    # Without words, any corrected artifact would erase the user's useful preview text.
    if corrected_words == []:
        return []

    scaffold_rows = normalise_scaffold_rows(live_segments)
    # A missing live transcript still allows text correction, but roles stay unknown.
    if scaffold_rows == []:
        return stamp_corrected_row_confidence(
            build_unscaffolded_segments(corrected_words, model_name),
            corrected_words,
            word_confidences,
        )

    allocated_chunks = allocate_words_to_scaffold(corrected_words, scaffold_rows)
    corrected_segments: list[dict[str, Any]] = []
    # Each live row becomes one corrected row when it receives ASR words.
    for row_index, (scaffold_row, row_words) in enumerate(
        zip(scaffold_rows, allocated_chunks, strict=False),
        start=1,
    ):
        # Rows with no allocated words would clutter the summary source list.
        if row_words == []:
            continue

        corrected_segments.append(
            {
                "segment_id": f"corrected-{row_index:04d}",
                "speaker_id": scaffold_row["speaker_id"],
                "role": scaffold_row["role"],
                "text": " ".join(row_words),
                "start": scaffold_row["start"],
                "end": scaffold_row["end"],
                "is_interim": False,
                "source": "post_visit_correction",
                "source_model": model_name,
            }
        )

    return stamp_corrected_row_confidence(
        prepare_corrected_source_segments(
            corrected_segments,
            corrected_words=corrected_words,
            word_timings=word_timings,
        ),
        corrected_words,
        word_confidences,
    )


def stamp_corrected_row_confidence(
    segments: list[dict[str, Any]],
    corrected_words: list[str],
    word_confidences: list[float] | None,
) -> list[dict[str, Any]]:
    """Attach per-row confidence to corrected rows built from ASR words.

    Runs after echo splits and role cleanup so each final visible row is
    located in the ASR word stream by its own words. Rows that cannot be
    located (live-text fallbacks, reshuffled rows) stay unmeasured and render
    exactly as they do today.

    Args:
        segments: Final corrected rows in visible order; empty passes through.
        corrected_words: Second-pass ASR words the rows were built from.
        word_confidences: Values aligned to `corrected_words`; None stamps nothing.

    Returns:
        The same rows, with `confidence` set where the word span was found.
    """
    # e.g. the clinician pressed Stop, the second ASR pass re-heard the visit,
    # and the summary is about to cite these rows as its source chips.
    # Without validated values every corrected row stays unmeasured.
    if not word_confidences:
        return segments

    # Each visible row is located independently so split rows stay accurate.
    for segment in segments:
        normalized_row_words = [
            normalize_corrected_role_word(word)
            for word in split_source_row_words(segment)
        ]
        span_start = locate_row_word_span(normalized_row_words, corrected_words)
        # Rows whose text is not ASR-owned have no confidence evidence.
        if span_start is None:
            continue

        row_confidence = transcript_row_confidence(
            word_confidences[span_start : span_start + len(normalized_row_words)]
        )
        # A row with no measured words keeps today's unstyled rendering.
        if row_confidence is not None:
            segment["confidence"] = row_confidence

    return segments


def normalise_scaffold_rows(
    live_segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return live rows usable as timing and role scaffolding.

    Args:
        live_segments: Preview rows from storage or browser; empty means no scaffold.

    Returns:
        Chronological rows with text, timing, role, and speaker fields normalized.
    """
    rows: list[dict[str, Any]] = []
    # Each preview row contributes the structure a clinician already reviewed.
    for segment in live_segments:
        text = str(segment.get("text", "")).strip()
        # Blank preview rows do not help align corrected words to the note.
        if text == "":
            continue

        start = _float_or_zero(segment.get("start"))
        end = _float_or_zero(segment.get("end"))
        # Bad timing should still produce a non-negative row for summary review.
        if end < start:
            end = start

        rows.append(
            {
                "speaker_id": str(segment.get("speaker_id") or "UNKNOWN"),
                "role": str(segment.get("role") or "UNKNOWN"),
                "text": text,
                "start": start,
                "end": end,
            }
        )

    rows.sort(key=lambda row: (row["start"], row["end"]))
    return rows


def allocate_words_to_scaffold(
    corrected_words: list[str],
    scaffold_rows: list[dict[str, Any]],
) -> list[list[str]]:
    """Split corrected words across existing preview rows.

    Args:
        corrected_words: Second-pass words; empty returns one empty chunk per row.
        scaffold_rows: Normalized live rows; empty returns no chunks.

    Returns:
        Word chunks aligned to scaffold rows; empty chunks mean that row is skipped.
    """
    # No rows means there is nowhere useful to place corrected words.
    if scaffold_rows == []:
        return []

    anchored_chunks = allocate_words_by_text_anchors(corrected_words, scaffold_rows)
    # Good anchors preserve user-visible turn boundaries better than row-size shares.
    if anchored_chunks is not None:
        return anchored_chunks

    live_word_counts = [
        max(1, len(split_words(str(row["text"])))) for row in scaffold_rows
    ]
    total_live_words = max(1, sum(live_word_counts))
    chunks: list[list[str]] = []
    word_cursor = 0

    # Allocate by preview word share so doctor/patient turn boundaries stay familiar.
    for row_index, live_word_count in enumerate(live_word_counts):
        # The final row receives all remaining ASR words so none are dropped.
        if row_index == len(live_word_counts) - 1:
            row_words = corrected_words[word_cursor:]
        else:
            share = live_word_count / total_live_words
            row_word_count = max(1, round(len(corrected_words) * share))
            row_words = corrected_words[word_cursor : word_cursor + row_word_count]
        word_cursor += len(row_words)
        chunks.append(row_words)

    return chunks


def allocate_words_by_text_anchors(
    corrected_words: list[str],
    scaffold_rows: list[dict[str, Any]],
) -> list[list[str]] | None:
    """Place corrected words by matching each preview row's text.

    Args:
        corrected_words: Second-pass words in spoken order; empty cannot be anchored.
        scaffold_rows: Normalized preview rows; empty means there is no row context.

    Returns:
        Per-row word chunks, or None when anchors are too weak and row-share fallback is safer.
    """
    # Empty inputs mean the user has no corrected words or no preview scaffold to align to.
    if corrected_words == [] or scaffold_rows == []:
        return None

    normalized_corrected_words = [
        _normalize_alignment_word(word) for word in corrected_words
    ]
    anchor_matches: list[AnchorMatch] = []
    word_cursor = 0
    confident_anchor_count = 0

    # Each preview row looks forward from the last match so row order stays chronological.
    for row_index, scaffold_row in enumerate(scaffold_rows):
        row_words = [
            _normalize_alignment_word(word)
            for word in split_words(scaffold_row["text"])
        ]
        row_words = [word for word in row_words if word]
        anchor_match = find_best_anchor_match(
            normalized_corrected_words,
            row_words,
            word_cursor,
        )
        # A missing row means second-pass ASR dropped something the user already saw.
        if anchor_match is None:
            anchor_match = AnchorMatch(word_cursor, word_cursor, 0.0)
        else:
            # A short row can match text already owned by the previous source chip.
            if anchor_match.start_index < word_cursor:
                anchor_match = AnchorMatch(
                    word_cursor,
                    max(word_cursor, anchor_match.end_index),
                    anchor_match.score,
                )

            # If clamping consumed the whole short row, keep the live row and preserve later anchors.
            if anchor_match.end_index <= anchor_match.start_index:
                anchor_match = AnchorMatch(word_cursor, word_cursor, 0.0)
            else:
                confident_anchor_count += 1

        # A malformed positive anchor would risk hiding later evidence behind row-share fallback.
        if (
            anchor_match.score > 0.0
            and anchor_match.end_index <= anchor_match.start_index
        ):
            anchor_match = AnchorMatch(
                word_cursor,
                word_cursor,
                0.0,
            )

        anchor_matches.append(anchor_match)
        word_cursor = anchor_match.end_index

    # Too few real anchors means estimates would be just another proportional allocator.
    if confident_anchor_count < max(2, len(scaffold_rows) // 3):
        return None

    return build_chunks_from_anchor_matches(
        corrected_words,
        scaffold_rows,
        anchor_matches,
    )


def find_best_anchor_match(
    corrected_words: list[str],
    row_words: list[str],
    cursor: int,
) -> AnchorMatch | None:
    """Find the corrected-word span that best matches one live row.

    Args:
        corrected_words: Normalized ASR words; empty means there is no candidate span.
        row_words: Normalized preview-row words; empty means the row cannot anchor.
        cursor: First likely corrected word for this row; lower values revisit old text.

    Returns:
        Best matching span, or None when no confident user-visible replacement exists.
    """
    # Blank preview rows cannot tell the user where corrected words belong.
    if corrected_words == [] or row_words == []:
        return None

    search_start = max(0, cursor - 2)
    search_end = min(
        len(corrected_words),
        cursor + max(12, len(row_words) * 3 + 8),
    )
    shortest_span = max(1, len(row_words) - 3)
    longest_span = min(
        max(shortest_span, len(row_words) + 6),
        search_end - search_start,
    )
    best_match: AnchorMatch | None = None

    # Try plausible spans near the current cursor; this keeps turn order stable for the user.
    for start_index in range(search_start, search_end):
        max_end = min(search_end, start_index + longest_span)
        min_end = min(max_end, start_index + shortest_span)
        # Each candidate window is scored against the preview text for this row.
        for end_index in range(min_end, max_end + 1):
            candidate_words = corrected_words[start_index:end_index]
            score = SequenceMatcher(None, row_words, candidate_words).ratio()
            # Keep the strongest local match so ASR insertions like "uh" are tolerated.
            if best_match is None or score > best_match.score:
                best_match = AnchorMatch(start_index, end_index, score)

    # A missing match means the row gets no safe corrected text anchor.
    if best_match is None:
        return None

    required_score = (
        _MIN_SHORT_ANCHOR_SCORE if len(row_words) <= 2 else _MIN_ANCHOR_SCORE
    )
    # Weak anchors would move words to rows the user did not actually hear there.
    if best_match.score < required_score:
        return None

    return best_match


def build_chunks_from_anchor_matches(
    corrected_words: list[str],
    scaffold_rows: list[dict[str, Any]],
    anchor_matches: list[AnchorMatch],
) -> list[list[str]]:
    """Build per-row corrected chunks from anchor spans and in-between gaps.

    Args:
        corrected_words: Original ASR display words with punctuation preserved.
        scaffold_rows: Preview rows whose role/timing scaffold is kept.
        anchor_matches: Monotonic anchor spans, one for each scaffold row.

    Returns:
        Corrected word chunks aligned to each preview row.
    """
    chunks: list[list[str]] = [[] for _row in scaffold_rows]
    # Words before the first anchor are audible opening context for the first row.
    if anchor_matches and anchor_matches[0].start_index > 0:
        chunks[0].extend(corrected_words[: anchor_matches[0].start_index])

    # Each anchor contributes its matched words, then hands the gap to a neighbor.
    for row_index, anchor_match in enumerate(anchor_matches):
        is_missing_anchor = is_anchor_match_missing(anchor_match)
        append_words_for_anchor_match(
            chunks,
            row_index,
            corrected_words,
            scaffold_rows,
            anchor_match,
        )
        next_match = (
            anchor_matches[row_index + 1]
            if row_index + 1 < len(anchor_matches)
            else None
        )
        gap_words = corrected_words[
            anchor_match.end_index : (
                next_match.start_index if next_match else len(corrected_words)
            )
        ]
        # Empty gaps mean anchors touched and there are no loose words to place.
        if gap_words == []:
            continue

        # Missing-anchor rows are live fallbacks; loose corrected words belong to a real anchor.
        if is_missing_anchor:
            append_gap_after_missing_anchor(chunks, gap_words, row_index, next_match)
            continue

        # A final filler starts the next turn; preceding words may complete the current cue.
        if (
            len(gap_words) > 1
            and _normalize_alignment_word(gap_words[-1]) in CORRECTED_ROLE_FILLER_WORDS
        ):
            current_with_gap = _normalize_alignment_phrase(
                f"{scaffold_rows[row_index].get('text', '')} {' '.join(gap_words[:-1])}"
            )
            # Keep phrase-completing words with the current row and move only the filler ahead.
            if (
                role_from_corrected_phrase(current_with_gap) is not None
                and next_match is not None
            ):
                chunks[row_index].extend(gap_words[:-1])
                chunks[row_index + 1].append(gap_words[-1])
                continue

        target_index = choose_gap_target_row(
            gap_words,
            scaffold_rows,
            row_index,
            row_index + 1 if next_match else None,
        )
        chunks[target_index].extend(gap_words)

    return chunks


def append_words_for_anchor_match(
    chunks: list[list[str]],
    row_index: int,
    corrected_words: list[str],
    scaffold_rows: list[dict[str, Any]],
    anchor_match: AnchorMatch,
) -> None:
    """Add words for one corrected row, using live text when ASR dropped it.

    Args:
        chunks: Per-row output being built; empty row means nothing visible yet.
        row_index: Visible row receiving words for the stopped visit.
        corrected_words: Second-pass ASR words; empty means only live fallback can render.
        scaffold_rows: Live transcript rows the user already saw; empty is not passed here.
        anchor_match: Text match for this row; empty span means ASR skipped the row.

    Returns:
        None; chunks are updated in place for the corrected transcript artifact.
    """
    # If ASR skipped a visible row, keep the live text the clinician already saw.
    if is_anchor_match_missing(anchor_match):
        chunks[row_index].extend(split_words(str(scaffold_rows[row_index]["text"])))
        return

    chunks[row_index].extend(
        corrected_words[anchor_match.start_index : anchor_match.end_index]
    )


def is_anchor_match_missing(anchor_match: AnchorMatch) -> bool:
    """Report whether a corrected row needs live-text fallback.

    Args:
        anchor_match: Candidate row match; zero score and empty span means no ASR anchor.

    Returns:
        True when the user-visible row should keep live preview text.
    """
    return (
        anchor_match.score == 0.0 and anchor_match.start_index == anchor_match.end_index
    )


def append_gap_after_missing_anchor(
    chunks: list[list[str]],
    gap_words: list[str],
    row_index: int,
    next_match: AnchorMatch | None,
) -> None:
    """Move loose corrected words away from a live-fallback row.

    Args:
        chunks: Per-row output being built; empty rows are skipped later.
        gap_words: Corrected words after a missing row; empty means nothing to preserve.
        row_index: Live-fallback row that should not receive unrelated ASR words.
        next_match: Later anchor if one exists; null means this is the final row.

    Returns:
        None; chunks are updated in place for the corrected transcript artifact.
    """
    # If a later anchor exists, it should own corrected words after the fallback row.
    if next_match is not None:
        chunks[row_index + 1].extend(gap_words)
        return

    # Trailing corrected words stay visible by attaching to the previous real row.
    if row_index > 0:
        chunks[row_index - 1].extend(gap_words)
        return

    chunks[row_index].extend(gap_words)


def choose_gap_target_row(
    gap_words: list[str],
    scaffold_rows: list[dict[str, Any]],
    current_row_index: int,
    next_row_index: int | None,
) -> int:
    """Choose which neighboring corrected row receives unanchored words.

    Args:
        gap_words: ASR words between two anchors; empty means no choice is needed.
        scaffold_rows: Preview rows used as role/timing context.
        current_row_index: Row before the gap.
        next_row_index: Row after the gap; null means this is the final trailing gap.

    Returns:
        Index of the row that should receive the loose words.
    """
    # A trailing gap has no later row, so it stays with the current audible turn.
    if next_row_index is None:
        return current_row_index

    current_role = str(scaffold_rows[current_row_index].get("role", "UNKNOWN"))
    next_role = str(scaffold_rows[next_row_index].get("role", "UNKNOWN"))
    first_gap_word = _normalize_alignment_word(gap_words[0])
    current_with_gap = _normalize_alignment_phrase(
        f"{scaffold_rows[current_row_index].get('text', '')} {' '.join(gap_words)}"
    )

    # Tiny trailing words can complete a cue phrase like "I'm sorry to hear that".
    if len(gap_words) <= 2 and role_from_corrected_phrase(current_with_gap) is not None:
        return current_row_index

    # Fillers like "oh" usually introduce the user's next utterance.
    if first_gap_word in CORRECTED_ROLE_FILLER_WORDS and next_role == "PATIENT":
        return next_row_index

    # A tiny gap after a doctor prompt usually completes the doctor's question.
    if current_role == "DOCTOR" and next_role != "DOCTOR" and len(gap_words) <= 2:
        return current_row_index

    # A tiny non-doctor tail before a doctor prompt usually completes the patient's answer.
    if (
        current_role == "PATIENT"
        and next_role == "DOCTOR"
        and len(gap_words) <= 2
        and first_gap_word not in DOCTOR_CONNECTOR_STARTS
    ):
        return current_row_index

    return next_row_index


def _normalize_alignment_phrase(text: str) -> str:
    """Normalize a phrase for cue and anchor matching.

    Args:
        text: User-visible transcript text; empty produces an empty cue phrase.

    Returns:
        Space-joined lowercase words with punctuation removed.
    """
    return " ".join(
        word
        for word in (
            _normalize_alignment_word(raw_word) for raw_word in split_words(text)
        )
        if word
    )


def _normalize_alignment_word(word: str) -> str:
    """Normalize one word for corrected/live text matching.

    Args:
        word: Transcript token with punctuation; empty means no anchor term.

    Returns:
        Lowercase alphanumeric token; empty means punctuation-only input.
    """
    return re.sub(r"[^a-z0-9]+", "", word.lower())


def build_unscaffolded_segments(
    corrected_words: list[str],
    model_name: str,
) -> list[dict[str, Any]]:
    """Create generic corrected rows when live timing is unavailable.

    Args:
        corrected_words: Second-pass ASR words; empty means no corrected transcript.
        model_name: ASR model id recorded in row provenance.

    Returns:
        UNKNOWN-role rows for fallback review; empty means no ASR text existed.
    """
    segments: list[dict[str, Any]] = []
    # Review chunks are capped so a fallback note does not cite one giant row.
    for row_index, start_word in enumerate(
        range(0, len(corrected_words), DEFAULT_MAX_WORDS_WITHOUT_SCAFFOLD),
        start=1,
    ):
        row_words = corrected_words[
            start_word : start_word + DEFAULT_MAX_WORDS_WITHOUT_SCAFFOLD
        ]
        segments.append(
            {
                "segment_id": f"corrected-{row_index:04d}",
                "speaker_id": "UNKNOWN",
                "role": "UNKNOWN",
                "text": " ".join(row_words),
                "start": float(row_index - 1),
                "end": float(row_index),
                "is_interim": False,
                "source": "post_visit_correction",
                "source_model": model_name,
            }
        )

    return segments


def _float_or_zero(value: Any) -> float:
    """Convert loose JSON timing values into seconds for corrected rows.

    Args:
        value: Browser/server timing value; null or invalid values mean `0.0`.

    Returns:
        Parsed float seconds, or `0.0` when timing is unavailable.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
