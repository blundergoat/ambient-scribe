"""
Post-visit transcript correction for stopped consultations.

The live UI still shows the realtime preview, but the summary path can use a
slower artifact after the user stops recording. This module runs a second ASR
pass over retained PCM audio, maps the corrected text onto the rows the user
already saw, and returns corrected rows that storage and summary citation code
can consume without changing live Mercure contracts.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
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
    "nvidia/parakeet-tdt-0.6b-v3",
)
DEFAULT_MAX_WORDS_WITHOUT_SCAFFOLD = 18
_AUDIO_SAMPLE_RATE = 16000
_MIN_ANCHOR_SCORE = 0.48
_MIN_SHORT_ANCHOR_SCORE = 0.86


class PostVisitCorrectionError(RuntimeError):
    """
    Raised when the correction pass cannot create user-visible rows.

    Use this for recoverable post-stop failures: the browser can still
    summarize the live preview, but the user should not be told that a
    corrected transcript exists.
    """


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
    """

    segments: list[dict[str, Any]]
    model_name: str
    word_count: int
    source: str = "post_visit_correction"


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


def run_post_visit_correction(
    *,
    pcm_audio: bytes,
    live_segments: list[dict[str, Any]],
    model_name: str = DEFAULT_POST_VISIT_ASR_MODEL,
    transcribe_audio_file: Callable[[str, str], PostVisitTranscription | str] | None = None,
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
        transcript_text, raw_word_timings, raw_word_confidences = (
            coerce_post_visit_transcription(transcriber(selected_model_name, str(audio_path)))
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
        },
    )

    return PostVisitCorrectionResult(
        segments=corrected_segments,
        model_name=selected_model_name,
        word_count=len(words),
    )


def transcribe_audio_with_nemo(model_name: str, audio_path: str) -> PostVisitTranscription:
    """Transcribe one WAV with a lazily loaded NeMo ASR checkpoint.

    Args:
        model_name: NVIDIA/NeMo model id; empty would fail model loading.
        audio_path: WAV path written from the stopped browser audio buffer.

    Returns:
        Transcript text plus best-effort word timings; empty text means the
        correction pass should fall back, None timings disable timing splits.

    Raises:
        PostVisitCorrectionError: When NeMo cannot load or transcribe the stopped visit audio.
    """
    try:
        import nemo.collections.asr as nemo_asr
    except Exception as import_error:  # pragma: no cover - depends on NeMo container.
        raise PostVisitCorrectionError(
            "NeMo ASR is unavailable for post-visit correction."
        ) from import_error

    try:
        asr_model = nemo_asr.models.ASRModel.from_pretrained(model_name=model_name)
        # Confidence is a pure observer of the decode (probe-proven byte-identical
        # output); enabling it lets corrected rows carry per-row confidence.
        try:
            enable_word_confidence_decoding(asr_model)
        except Exception:  # pragma: no cover - depends on NeMo decoding internals.
            # A model that cannot take the config still corrects text; its rows
            # simply render without confidence styling.
            logger.warning("post_visit_correction.confidence_enable_failed", exc_info=True)
        try:
            result = asr_model.transcribe(
                [audio_path],
                return_hypotheses=True,
                timestamps=True,
            )
        except TypeError:
            # An overridden POST_VISIT_ASR_MODEL may predate the timestamps flag; the
            # corrected note still renders from text alone, just without timing splits.
            result = asr_model.transcribe([audio_path], return_hypotheses=True)
    except Exception as transcribe_error:  # pragma: no cover - depends on GPU/model.
        raise PostVisitCorrectionError(
            f"Second-pass ASR failed for {model_name}: {transcribe_error}"
        ) from transcribe_error

    # No result means the model accepted audio but produced no reviewable text.
    if not result:
        return PostVisitTranscription(text="", word_timings=None)

    hypothesis = result[0]
    word_timings: list[dict[str, Any]] | None = None
    try:
        word_timings = word_timings_from_hypothesis(
            hypothesis,
            wav_duration_seconds(audio_path),
        ) or None
    except Exception:  # pragma: no cover - depends on NeMo hypothesis internals.
        # Timing is best-effort evidence; the corrected note must still render without it.
        logger.warning("post_visit_correction.word_timing_extraction_failed", exc_info=True)

    transcript_text = normalise_transcript_text(hypothesis)
    return PostVisitTranscription(
        text=transcript_text,
        word_timings=word_timings,
        word_confidences=word_confidences_for_display_words(
            hypothesis, split_words(transcript_text)
        ),
    )


def coerce_post_visit_transcription(
    transcription: PostVisitTranscription | str,
) -> tuple[str, list[dict[str, Any]] | None, list[float] | None]:
    """Normalise rich or plain transcriber output into text plus optional evidence.

    Args:
        transcription: Transcriber return value; plain strings come from timing-unaware seams.

    Returns:
        `(text, word_timings, word_confidences)`; None timings mean no timing-based
        split can run, None confidences leave corrected rows unmeasured.
    """
    # Evidence-aware transcribers return the richer shape with timing and confidence.
    if isinstance(transcription, PostVisitTranscription):
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


def normalise_scaffold_rows(live_segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
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

    live_word_counts = [max(1, len(split_words(str(row["text"])))) for row in scaffold_rows]
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

    normalized_corrected_words = [_normalize_alignment_word(word) for word in corrected_words]
    anchor_matches: list[AnchorMatch] = []
    word_cursor = 0
    confident_anchor_count = 0

    # Each preview row looks forward from the last match so row order stays chronological.
    for row_index, scaffold_row in enumerate(scaffold_rows):
        row_words = [_normalize_alignment_word(word) for word in split_words(scaffold_row["text"])]
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
        if anchor_match.score > 0.0 and anchor_match.end_index <= anchor_match.start_index:
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

    required_score = _MIN_SHORT_ANCHOR_SCORE if len(row_words) <= 2 else _MIN_ANCHOR_SCORE
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
    return anchor_match.score == 0.0 and anchor_match.start_index == anchor_match.end_index


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
        for word in (_normalize_alignment_word(raw_word) for raw_word in split_words(text))
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
