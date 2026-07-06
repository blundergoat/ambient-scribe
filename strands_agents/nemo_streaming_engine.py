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
    """

    speaker_slot: str
    text: str
    start: float
    end: float


@dataclass
class _WordEntry:
    """One heard word with the span of the step that first produced it."""

    text: str
    start: float
    end: float


# NeMo's cache-aware decoder freely rewrites roughly the last
# fix_prev_words_count + update_prev_words_sentence words; holding that many
# back is the streaming analog of the windowed engine's unstable-tail hold.
_MUTABLE_TAIL_WORDS = 10


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
        tail_rows = self._emit_stable_words(mutable_tail_words=0)
        return sorted(flushed + tail_rows, key=lambda row: row.start)

    def close(self) -> None:
        """Drop per-session streaming state so GPU memory returns promptly."""
        self._word_logs.clear()
        self._emitted_word_counts.clear()
        self._slot_last_burst_end.clear()
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
            self._absorb_step_hypotheses()
            emitted.extend(self._emit_stable_words(mutable_tail_words=_MUTABLE_TAIL_WORDS))

        return emitted

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

        if True:  # container-only debug copy
            import json as _json

            debug_rows = []
            for _idx, _hyp in enumerate(hypotheses):
                if _hyp is None:
                    debug_rows.append({"slot": _idx, "none": True})
                    continue
                _words = str(getattr(_hyp, "text", "") or "").split()
                _ts = getattr(_hyp, "timestamp", None)
                debug_rows.append(
                    {
                        "slot": _idx,
                        "n_words": len(_words),
                        "head": " ".join(_words[:4]),
                        "tail": " ".join(_words[-4:]),
                        "ts0": float(_ts[0]) if _ts is not None and len(_ts) else None,
                        "tsN": float(_ts[-1]) if _ts is not None and len(_ts) else None,
                    }
                )
            with open("/tmp/engine-debug.jsonl", "a") as _f:
                _f.write(
                    _json.dumps(
                        {
                            "step": self._step_index,
                            "offset": float(
                                getattr(self._streamer, "_offset_chunk_start_time", -1.0)
                            ),
                            "speakers": list(getattr(asr_state, "speakers", None) or []),
                            "slots": debug_rows,
                        }
                    )
                    + "\n"
                )
        for slot_index, hypothesis in enumerate(hypotheses):
            if hypothesis is None or not getattr(hypothesis, "text", None):
                continue

            words = str(hypothesis.text).split()
            word_log = self._word_logs.setdefault(slot_index, [])
            emitted_count = self._emitted_word_counts.get(slot_index, 0)

            # Update revisable words in place; count only revisions that would
            # have touched already-emitted text (they resync silently).
            for index in range(min(len(word_log), len(words))):
                if word_log[index].text != words[index]:
                    if index < emitted_count:
                        self.diagnostics.revision_resyncs += 1
                    word_log[index].text = words[index]

            # NeMo shrank the hypothesis: drop unemitted tail entries so the
            # log tracks reality; emitted words stay emitted (emit-once).
            if len(words) < len(word_log):
                del word_log[max(emitted_count, len(words)) :]

            appended = words[len(word_log) :]
            if appended:
                # Hypothesis token frames count the instance's own voiced time,
                # not session time, so word times come from the step clock: a
                # burst of words decoded this step was spoken between the
                # slot's previous burst and now (lag-capped), spread linearly.
                step_offset = float(
                    getattr(self._streamer, "_offset_chunk_start_time", 0.0) or 0.0
                )
                burst_end = step_offset + self._step_seconds
                burst_start = max(
                    self._slot_last_burst_end.get(slot_index, 0.0),
                    burst_end - max(len(appended) * 0.35, self._step_seconds),
                )
                per_word = (burst_end - burst_start) / len(appended)
                for position, _word in enumerate(appended):
                    word_log.append(
                        _WordEntry(
                            text=_word,
                            start=burst_start + position * per_word,
                            end=burst_start + (position + 1) * per_word,
                        )
                    )
                self._slot_last_burst_end[slot_index] = burst_end

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
        for slot_index, word_log in self._word_logs.items():
            emitted_count = self._emitted_word_counts.get(slot_index, 0)
            stable_count = max(emitted_count, len(word_log) - mutable_tail_words)
            if stable_count <= emitted_count:
                continue

            stable_words = word_log[emitted_count:stable_count]
            self._emitted_word_counts[slot_index] = stable_count
            row = EngineRow(
                speaker_slot=f"speaker_{slot_index}",
                text=" ".join(entry.text for entry in stable_words),
                start=stable_words[0].start,
                end=max(entry.end for entry in stable_words),
            )
            rows.append(row)
            self._count_emitted(row)

        return sorted(rows, key=lambda row: row.start)

    @property
    def pending_row_count(self) -> int:
        """Slots currently holding unemitted words (the held tail)."""
        return sum(
            1
            for slot_index, word_log in self._word_logs.items()
            if len(word_log) > self._emitted_word_counts.get(slot_index, 0)
        )

    def _count_emitted(self, row: EngineRow) -> None:
        """Track per-slot emission counts and late slot births for diagnostics."""
        counts = self.diagnostics.slot_row_counts
        if row.speaker_slot not in counts and row.start > _LATE_SLOT_SECONDS:
            self.diagnostics.late_slot_births += 1
        counts[row.speaker_slot] = counts.get(row.speaker_slot, 0) + 1
