"""
Session-long streaming NeMo engine (M22, behind NEMO_SESSION_ENGINE).

One engine instance per recording session wraps NVIDIA's `SpeakerTaggedASR`
composite so the Sortformer speaker cache owns speaker identity for the whole
consultation - replacing the windowed engine's per-window re-diarization and
overlap-vote stitching. Heavy imports (torch/NeMo) happen at construction so
the module stays importable in torch-free test environments.

The flag is PROCESS-LEVEL: all sessions in one server process use the same
engine, which is what makes the one-time streaming configuration of the shared
diar singleton safe (`NemoPipeline.create_streaming_engine`).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

from nemo_confidence import (
    transcript_row_confidence,
    word_confidences_for_display_words,
)

logger = logging.getLogger(__name__)

STREAMING_ENGINE_FLAG = "NEMO_SESSION_ENGINE"
_STREAMING_VALUE = "streaming"
_WINDOWED_VALUE = "windowed"

# One-step hysteresis: a speaker's newest text diff is held until a newer diff
# for that speaker arrives (or finalize flushes it), mirroring the windowed
# engine's unstable-tail hold for text NeMo may still revise.
_SIXTEEN_KHZ = 16000


def streaming_engine_enabled() -> bool:
    """Return whether new sessions should use the session-long streaming engine.

    Returns:
        True only for an explicit `streaming` value; unknown values fall back
        to the windowed engine so a typo cannot change the inference path.
    """
    configured = os.environ.get(STREAMING_ENGINE_FLAG, _WINDOWED_VALUE).strip().lower()
    if configured not in {_STREAMING_VALUE, _WINDOWED_VALUE, ""}:
        logger.warning(
            "nemo_streaming_engine.unknown_flag_value value=%s fallback=%s",
            configured,
            _WINDOWED_VALUE,
        )
    return configured == _STREAMING_VALUE


@dataclass
class EngineRow:
    """One stabilized per-step transcript row from the streaming engine.

    Attributes:
        speaker_slot: Cache slot label such as `speaker_0`; stable per session.
        text: Stabilized transcript text for this step span.
        start: Absolute session start seconds.
        end: Absolute session end seconds.
        confidence: How clearly the row was heard (0-1); None means this row
            carries no value and renders without confidence styling.
    """

    speaker_slot: str
    text: str
    start: float
    end: float
    confidence: float | None = None


@dataclass
class _WordEntry:
    """One heard word with the span of the step that first produced it.

    Confidence refreshes on every step while the word is still unemitted, so a
    word firms up (or weakens) as NeMo hears more context; None means the step
    that produced it carried no usable confidence.
    """

    text: str
    start: float
    end: float
    confidence: float | None = None


# NeMo's cache-aware decoder freely rewrites roughly the last
# fix_prev_words_count + update_prev_words_sentence words; holding that many
# back is the streaming analog of the windowed engine's unstable-tail hold.
_MUTABLE_TAIL_WORDS = 10
# Word bursts are assigned at most this far behind the step clock, and rows
# are released to the UI only once the step clock has passed this horizon -
# guaranteeing chronological emission across speaker slots.
_BURST_LOOKBACK_SECONDS = 5.0
# A slot whose hypothesis has not changed for this long is dormant: NeMo
# revisions land within a few steps, so its held tail is de facto stable and
# emits rather than pinning the release frontier (and the UI) indefinitely.
_DORMANT_SLOT_SECONDS = 12.0
# The last few words stay held even on dormant slots: a waking slot revises
# its extreme tail first, and draining those measurably cost slot purity.
_DORMANT_HELD_WORDS = 3
# After this long without any voiced activity a slot's held words cannot
# revise in practice; drain them fully so they render mid-session (the UI
# inserts them chronologically) instead of appearing only at finalize.
_DORMANT_FULL_DRAIN_SECONDS = 25.0


@dataclass
class EngineDiagnostics:
    """Identity evidence for the continuity log; never transcript text.

    Attributes:
        slot_row_counts: Emitted-row count per speaker cache slot.
        late_slot_births: Slots first seen after the establishment window.
        revision_resyncs: Times NeMo revised already-emitted text and the
            engine resynced instead of re-emitting.
        steps: Streaming steps executed so far.
    """

    slot_row_counts: dict[str, int] = field(default_factory=dict)
    late_slot_births: int = 0
    revision_resyncs: int = 0
    steps: int = 0


# Slots first seen after this much audio indicate identity trouble, because a
# dyadic consultation establishes both voices early.
_LATE_SLOT_SECONDS = 60.0


class StreamingSessionEngine:
    """Per-session wrapper around NeMo's SpeakerTaggedASR composite pipeline.

    All methods are SYNCHRONOUS and GPU-bound - callers run them on the same
    executor as the windowed engine. All streaming state (encoder caches,
    hypotheses, diar cache) is held by this instance via NeMo's caller-held
    state objects; the shared models stay pure compute (ADR-001).
    """

    def __init__(self, session_id: str, asr_model: Any, diar_model: Any) -> None:
        """Build the composite streamer for one recording session.

        Args:
            session_id: Recording UUID, for logs only.
            asr_model: Shared multitalker Parakeet singleton, already loaded.
            diar_model: Shared streaming Sortformer singleton, already
                configured for streaming by the pipeline factory.
        """
        import torch
        from nemo.collections.asr.parts.utils.multispk_transcribe_utils import (
            SpeakerTaggedASR,
        )
        from nemo.collections.asr.parts.utils.streaming_utils import (
            CacheAwareStreamingAudioBuffer,
        )
        from omegaconf import OmegaConf

        self.session_id = session_id
        self._torch = torch
        self._asr_model = asr_model

        # deploy_mode is NeMo's live entry point: no file manifests, one
        # synthetic streaming session. Field names mirror the reference CLI's
        # MultitalkerTranscriptionConfig (a script-local dataclass).
        self._cfg = OmegaConf.create(
            {
                "deploy_mode": True,
                "manifest_file": None,
                "audio_file": None,
                "batch_size": 1,
                "max_num_of_spks": 4,
                "att_context_size": [70, 13],
                "fix_prev_words_count": 5,
                "update_prev_words_sentence": 5,
                "ignored_initial_frame_steps": 5,
                "sent_break_sec": 5.0,
                "cache_gating": True,
                "cache_gating_buffer_size": 2,
                "binary_diar_preds": False,
                "masked_asr": True,
                "mask_preencode": False,
                "single_speaker_mode": False,
                "parallel_speaker_strategy": True,
                "verbose": False,
                "word_window": 50,
                "colored_text": False,
                "print_time": False,
                "real_time_mode": False,
            }
        )

        self._streamer = SpeakerTaggedASR(self._cfg, asr_model, diar_model)
        self._buffer = CacheAwareStreamingAudioBuffer(
            model=asr_model,
            online_normalization=False,
            pad_and_drop_preencoded=False,
        )
        self._buffer_iter: Any = None
        self._stream_id: int = -1
        self._step_index: int = 0
        self._pre_encode_drop: int = 0
        self._word_logs: dict[int, list[_WordEntry]] = {}
        self._emitted_word_counts: dict[int, int] = {}
        self._slot_last_burst_end: dict[int, float] = {}
        self._ordered_pending: list[EngineRow] = []
        # Per-slot (cumulative voiced frames -> wall seconds) samples from the
        # diarizer's activity stream. A word's token timestamp counts its
        # instance's voiced frames, so inverting this curve recovers the
        # word's true spoken time instead of its (lagged) decode time.
        self._slot_frame_ledgers: dict[int, list[tuple[int, float]]] = {}
        self._diar_frames_seen: int = 0
        self._slot_token_counts: dict[int, int] = {}
        self._frame_len_sec: float = 0.08
        self._step_seconds: float = (self._cfg.att_context_size[1] + 1) * 0.08
        self.diagnostics = EngineDiagnostics()

        logger.info(
            "nemo_streaming_engine.created",
            extra={"session_id": session_id},
        )

    def feed(self, pcm_audio: bytes) -> list[EngineRow]:
        """Append one browser PCM chunk and run every ready streaming step.

        Args:
            pcm_audio: 16 kHz mono 16-bit PCM bytes; empty feeds nothing.

        Returns:
            Rows stabilized by this chunk's steps (one-step hysteresis per
            speaker); empty while text is still inside the mutable window.
        """
        if pcm_audio == b"":
            return []

        import numpy as np

        audio = (
            np.frombuffer(pcm_audio, dtype=np.int16).astype(np.float32) / 32768.0
        )
        _, _, stream_id = self._buffer.append_audio(audio, stream_id=self._stream_id)
        # NeMo's create branch returns -1 for the first stream; pinning slot 0
        # keeps later appends extending THIS stream instead of padding a new
        # one per chunk (which grows the batch and breaks the diar state).
        self._stream_id = int(stream_id) if int(stream_id) >= 0 else 0

        return self._run_ready_steps(final=False)

    def flush(self) -> list[EngineRow]:
        """Emit every held row when the session ends.

        Returns:
            All pending rows in chronological order; empty when nothing is held.
        """
        import numpy as np

        # Pad one step of silence so the sub-chunk audio tail yields a final
        # (buffer-empty) step, flushing the decoder's gated caches.
        if self._buffer is not None and self._stream_id >= 0:
            silence = np.zeros(int(self._step_seconds * 2 * _SIXTEEN_KHZ), dtype=np.float32)
            self._buffer.append_audio(silence, stream_id=self._stream_id)
        flushed = self._run_ready_steps(final=True)
        self._ordered_pending.extend(self._emit_stable_words(mutable_tail_words=0))
        tail_rows = self._release_ordered_rows(final=True)
        return sorted(flushed + tail_rows, key=lambda row: row.start)

    def close(self) -> None:
        """Drop per-session streaming state so GPU memory returns promptly."""
        self._word_logs.clear()
        self._emitted_word_counts.clear()
        self._slot_last_burst_end.clear()
        self._ordered_pending.clear()
        self._slot_frame_ledgers.clear()
        self._slot_token_counts.clear()
        self._buffer_iter = None
        self._buffer = None
        self._streamer = None

    def _run_ready_steps(self, *, final: bool) -> list[EngineRow]:
        """Step the composite over every full chunk currently buffered."""
        emitted: list[EngineRow] = []
        if self._streamer is None:
            return emitted

        if self._buffer_iter is None:
            self._buffer_iter = iter(self._buffer)

        while True:
            # NeMo's iterator yields PARTIAL chunks near the buffer end and
            # advances the cursor a full shift regardless, truncating and
            # skipping audio. At real-time pacing the buffer drains every
            # feed, so mid-stream steps must wait for one full chunk; only
            # the final flush may consume the padded tail.
            if not final and self._feature_frames_available() < self._full_chunk_frames_needed():
                break
            try:
                chunk_audio, chunk_lengths = next(self._buffer_iter)
            except StopIteration:
                # The iterator is positional over the growing buffer, so it
                # resumes after later appends; recreate it lazily either way.
                self._buffer_iter = None
                break

            # After the first step the encoder output repeats pre-encode
            # context that must be dropped, exactly as the reference CLI does.
            drop_extra = (
                0
                if self._step_index == 0
                else int(self._asr_model.encoder.streaming_cfg.drop_extra_pre_encoded)
            )
            with self._torch.inference_mode():
                self._streamer.perform_parallel_streaming_stt_spk(
                    step_num=self._step_index,
                    chunk_audio=chunk_audio,
                    chunk_lengths=chunk_lengths,
                    is_buffer_empty=self._buffer.is_buffer_empty(),
                    drop_extra_pre_encoded=drop_extra,
                )
            self._step_index += 1
            self.diagnostics.steps = self._step_index
            self._record_diar_activity()
            self._absorb_step_hypotheses()
            self._ordered_pending.extend(
                self._emit_stable_words(mutable_tail_words=_MUTABLE_TAIL_WORDS)
            )

        emitted.extend(self._release_ordered_rows(final=final))
        return emitted

    def _record_diar_activity(self) -> None:
        """Sample each slot's voiced-frame count against the wall clock.

        The diarizer's cumulative prediction stream says which slots were
        speaking in each 80ms frame. One (voiced_frames, wall_seconds) sample
        per active slot per step builds the curve that turns a word's
        instance-local token timestamp into its true spoken time.
        """
        diar_states = getattr(self._streamer.instance_manager, "diar_states", None)
        preds = getattr(diar_states, "diar_pred_out_stream", None)
        if preds is None or preds.ndim != 3:
            return

        total_frames = int(preds.size(1))
        if total_frames <= self._diar_frames_seen:
            return

        new_preds = preds[0, self._diar_frames_seen :, :]
        base_frame = self._diar_frames_seen
        self._diar_frames_seen = total_frames

        for slot_index in range(int(preds.size(2))):
            active = (new_preds[:, slot_index] > 0.5).nonzero()
            if active.numel() == 0:
                continue
            ledger = self._slot_frame_ledgers.setdefault(slot_index, [])
            voiced_before = ledger[-1][0] if ledger else 0
            # One sample per step keeps the ledger compact; the mid-frame of
            # this step's activity anchors the wall time.
            active_indices = active.flatten().tolist()
            mid_wall = (base_frame + active_indices[len(active_indices) // 2]) * self._frame_len_sec
            ledger.append((voiced_before + len(active_indices), float(mid_wall)))
            self._slot_last_burst_end[slot_index] = float(
                (base_frame + active_indices[-1]) * self._frame_len_sec
            )

    def _wall_time_for_voiced_frame(self, slot_index: int, voiced_frame: float) -> float:
        """Map an instance-local voiced-frame index to wall seconds."""
        ledger = self._slot_frame_ledgers.get(slot_index)
        if not ledger:
            return float(
                getattr(self._streamer, "_offset_chunk_start_time", 0.0) or 0.0
            )

        previous_frames, previous_wall = 0, ledger[0][1]
        for cumulative_frames, wall_seconds in ledger:
            if voiced_frame <= cumulative_frames:
                span_frames = max(1, cumulative_frames - previous_frames)
                fraction = max(0.0, voiced_frame - previous_frames) / span_frames
                return previous_wall + fraction * (wall_seconds - previous_wall)
            previous_frames, previous_wall = cumulative_frames, wall_seconds
        return ledger[-1][1]

    def _absorb_step_hypotheses(self) -> None:
        """Fold this step's per-speaker hypotheses into the word logs.

        Appended words enter the log with this step's span; when NeMo revises
        words inside the mutable window, their text is updated in place and
        their first-heard span is kept, so emitted history never re-emits.
        """
        asr_states = getattr(self._streamer.instance_manager, "batch_asr_states", [])
        if not asr_states:
            return
        asr_state = asr_states[0]

        hypotheses = getattr(asr_state, "previous_hypothesis", None) or []

        for slot_index, hypothesis in enumerate(hypotheses):
            if hypothesis is None or not getattr(hypothesis, "text", None):
                continue

            words = str(hypothesis.text).split()
            word_log = self._word_logs.setdefault(slot_index, [])
            emitted_count = self._emitted_word_counts.get(slot_index, 0)
            # Confidence recomputes over the whole hypothesis each step, so
            # held words keep firming up until they emit; a misaligned list is
            # discarded rather than styling the wrong rows.
            word_values = word_confidences_for_display_words(hypothesis, words)

            # Update revisable words in place; count only revisions that would
            # have touched already-emitted text (they resync silently).
            for index in range(min(len(word_log), len(words))):
                if word_log[index].text != words[index]:
                    if index < emitted_count:
                        self.diagnostics.revision_resyncs += 1
                    word_log[index].text = words[index]
                # Fresh confidence describes this word better than the step
                # that first produced it.
                if word_values is not None:
                    word_log[index].confidence = word_values[index]

            # NeMo shrank the hypothesis: drop unemitted tail entries so the
            # log tracks reality; emitted words stay emitted (emit-once).
            if len(words) < len(word_log):
                del word_log[max(emitted_count, len(words)) :]

            appended_base_index = len(word_log)
            appended = words[appended_base_index:]
            # Newly heard words join the log with their spoken span and the
            # confidence NeMo reported for them this step.
            if appended:
                word_log.extend(
                    self._appended_word_entries(
                        slot_index, hypothesis, appended, appended_base_index, word_values
                    )
                )

    def _appended_word_entries(
        self,
        slot_index: int,
        hypothesis: Any,
        appended: list[str],
        appended_base_index: int,
        word_values: list[float] | None,
    ) -> list[_WordEntry]:
        """Build log entries for the words a step just added to one slot.

        Token timestamps count the instance's voiced frames; the diar-activity
        ledger inverts them to true spoken times. Tokens (BPE) outnumber words,
        so this step's new tokens are spread proportionally across the new words.

        Args:
            slot_index: Speaker cache slot the words belong to.
            hypothesis: This step's slot hypothesis; a tensor-less `timestamp`
                falls back to step-clock word spans.
            appended: Newly heard words; callers skip empty batches.
            appended_base_index: Hypothesis word index of the first new word.
            word_values: Full-hypothesis confidence; None leaves words unmeasured.

        Returns:
            One `_WordEntry` per appended word, in spoken order.
        """
        timestamps = getattr(hypothesis, "timestamp", None)
        total_tokens = len(timestamps) if timestamps is not None else 0
        tokens_before = self._slot_token_counts.get(slot_index, 0)
        new_tokens = max(0, total_tokens - tokens_before)
        self._slot_token_counts[slot_index] = total_tokens

        entries: list[_WordEntry] = []
        # Each new word gets its own share of this step's new tokens.
        for position, appended_word in enumerate(appended):
            # Real token timestamps recover the word's true spoken time.
            if new_tokens > 0 and timestamps is not None:
                token_lo = tokens_before + int(position * new_tokens / len(appended))
                token_hi = tokens_before + int(
                    (position + 1) * new_tokens / len(appended)
                )
                token_hi = min(max(token_hi, token_lo + 1), total_tokens)
                word_start = self._wall_time_for_voiced_frame(
                    slot_index, float(timestamps[token_lo])
                )
                word_end = self._wall_time_for_voiced_frame(
                    slot_index, float(timestamps[token_hi - 1]) + 1.0
                )
            else:
                # Without token evidence the step clock is the best span.
                step_offset = float(
                    getattr(self._streamer, "_offset_chunk_start_time", 0.0) or 0.0
                )
                word_start = step_offset
                word_end = step_offset + self._frame_len_sec
            entries.append(
                _WordEntry(
                    text=appended_word,
                    start=word_start,
                    end=max(word_end, word_start + 0.05),
                    confidence=(
                        word_values[appended_base_index + position]
                        if word_values is not None
                        else None
                    ),
                )
            )

        return entries

    def _emit_stable_words(self, *, mutable_tail_words: int) -> list[EngineRow]:
        """Emit each slot's stable unemitted words as one row per slot.

        Args:
            mutable_tail_words: Words held back for NeMo revision; zero drains
                everything at finalize.

        Returns:
            Newly stable rows in chronological order; empty means all text is
            still inside the mutable window.
        """
        rows: list[EngineRow] = []
        offset = float(
            getattr(self._streamer, "_offset_chunk_start_time", 0.0) or 0.0
        )
        for slot_index, word_log in self._word_logs.items():
            emitted_count = self._emitted_word_counts.get(slot_index, 0)
            slot_tail_words = mutable_tail_words
            # Dormant slots cannot revise anymore; drain their held tail so
            # the chronological release frontier keeps moving.
            last_update = self._slot_last_burst_end.get(slot_index, 0.0)
            if slot_tail_words and offset - last_update > _DORMANT_SLOT_SECONDS:
                slot_tail_words = (
                    0
                    if offset - last_update > _DORMANT_FULL_DRAIN_SECONDS
                    else min(slot_tail_words, _DORMANT_HELD_WORDS)
                )
            stable_count = max(emitted_count, len(word_log) - slot_tail_words)
            if stable_count <= emitted_count:
                continue

            stable_words = word_log[emitted_count:stable_count]
            self._emitted_word_counts[slot_index] = stable_count

            # With honest word times, a >1.5s silence inside one batch means
            # separate utterances - emit them as separate rows.
            groups: list[list[_WordEntry]] = [[stable_words[0]]]
            for entry in stable_words[1:]:
                if entry.start - groups[-1][-1].end > 1.5:
                    groups.append([entry])
                else:
                    groups[-1].append(entry)

            for group in groups:
                row = EngineRow(
                    speaker_slot=f"speaker_{slot_index}",
                    text=" ".join(entry.text for entry in group),
                    start=group[0].start,
                    end=max(entry.end for entry in group),
                    confidence=transcript_row_confidence(
                        entry.confidence for entry in group
                    ),
                )
                rows.append(row)
                self._count_emitted(row)

        return sorted(rows, key=lambda row: row.start)

    def _release_ordered_rows(self, *, final: bool) -> list[EngineRow]:
        """Release pending rows in chronological order up to the safe horizon.

        Speaker slots stabilize at different rates, so a slot can produce a
        row that starts earlier than one already stabilized elsewhere. Rows
        wait until the step clock passes their start by the burst lookback,
        after which no slot can produce an earlier row.

        Args:
            final: True releases everything at session end.

        Returns:
            Chronologically ordered rows safe to show the clinician.
        """
        if not self._ordered_pending:
            return []

        if final:
            released = sorted(self._ordered_pending, key=lambda row: row.start)
            self._ordered_pending = []
            return released

        offset = float(
            getattr(self._streamer, "_offset_chunk_start_time", 0.0) or 0.0
        )
        # A slot's held (still-mutable) words can be older than the step
        # clock, so the release horizon must trail BOTH the clock and every
        # slot's oldest unstable word - otherwise a slow-stabilizing slot's
        # first row could render after a later row already did.
        horizon = min(offset - _BURST_LOOKBACK_SECONDS, self._stability_frontier())
        released = sorted(
            (row for row in self._ordered_pending if row.start <= horizon),
            key=lambda row: row.start,
        )
        self._ordered_pending = [
            row for row in self._ordered_pending if row.start > horizon
        ]
        return released

    def _stability_frontier(self) -> float:
        """Earliest start any slot's still-unstable words could emit with.

        A dormant slot's final held words are excluded: with honest word
        times their old starts would pin the frontier (and the UI) until
        finalize. They emit on wake or flush; the rare bounded ordering slip
        that allows is far better than stalling live emission.
        """
        offset = float(
            getattr(self._streamer, "_offset_chunk_start_time", 0.0) or 0.0
        )
        frontier = float("inf")
        for slot_index, word_log in self._word_logs.items():
            emitted_count = self._emitted_word_counts.get(slot_index, 0)
            unemitted = len(word_log) - emitted_count
            if unemitted <= 0:
                continue
            last_activity = self._slot_last_burst_end.get(slot_index, 0.0)
            if (
                unemitted <= _DORMANT_HELD_WORDS
                and offset - last_activity > _DORMANT_SLOT_SECONDS
            ):
                continue
            frontier = min(frontier, word_log[emitted_count].start)
        return frontier

    def _feature_frames_available(self) -> int:
        """Unconsumed preprocessed feature frames currently buffered."""
        buffer_tensor = getattr(self._buffer, "buffer", None)
        if buffer_tensor is None:
            return 0
        return int(buffer_tensor.size(-1)) - int(self._buffer.buffer_idx)

    def _full_chunk_frames_needed(self) -> int:
        """Feature frames one full streaming chunk needs at the current cursor."""
        chunk_size = self._asr_model.encoder.streaming_cfg.chunk_size
        if isinstance(chunk_size, (list, tuple)):
            first_chunk, later_chunks = int(chunk_size[0]), int(chunk_size[1])
        else:
            first_chunk = later_chunks = int(chunk_size)
        buffer_idx = int(getattr(self._buffer, "buffer_idx", 0) or 0)
        return first_chunk if buffer_idx == 0 else later_chunks

    @property
    def pending_row_count(self) -> int:
        """Slots currently holding unemitted words (the held tail)."""
        unstable_slots = sum(
            1
            for slot_index, word_log in self._word_logs.items()
            if len(word_log) > self._emitted_word_counts.get(slot_index, 0)
        )
        return unstable_slots + len(self._ordered_pending)

    def _count_emitted(self, row: EngineRow) -> None:
        """Track per-slot emission counts and late slot births for diagnostics."""
        counts = self.diagnostics.slot_row_counts
        if row.speaker_slot not in counts and row.start > _LATE_SLOT_SECONDS:
            self.diagnostics.late_slot_births += 1
        counts[row.speaker_slot] = counts.get(row.speaker_slot, 0) + 1
