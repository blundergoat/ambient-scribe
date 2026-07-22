"""Correction-time speaker rebuild behind NEMO_CORRECTION_REDIARIZATION.

After a stopped visit's second ASR pass, this leg rebuilds speaker structure
from the retained full audio (Sortformer), assigns the correction's own word
timings to the rebuilt turns, asks the role agent to label the rebuilt voices,
and decides each fold-suspect span with the frozen ADR-010 two-witness policy.
Only row-level roles inside spans both witnesses agree on may change; every
other outcome leaves the clinician's transcript untouched. The leg is
default-off, runs inside the correction executor slot, and a failure here can
never take the corrected transcript with it.

The decision table is the single runtime home of the ADR-010 policy;
`scripts/rediar-span-comparer.py` delegates here for offline QA runs.
"""

from __future__ import annotations

import gc
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

REDIARIZATION_FLAG = "NEMO_CORRECTION_REDIARIZATION"
# M02's capacity ladder proved full-audio Sortformer through its longest leg,
# c07 at 858.2s (two deterministic runs, VRAM flat - rediar-m02-capacity
# ledger). 860 admits that proven maximum plus sub-second retention jitter;
# longer retained audio (possible up to the 900s buffer cap) stays unproven
# and gates off.
REDIAR_MAX_AUDIO_SECONDS = 860.0
# Frozen operating point from the rediar spikes: gap words may snap to the
# nearest rebuilt turn edge within this window, farther words stay unassigned.
ASSIGNMENT_TOLERANCE_SECONDS = 0.5
DEFAULT_SORTFORMER_MODEL = "nvidia/diar_streaming_sortformer_4spk-v2.1"

SUPPORTED_VISIBLE_ROLES = {"DOCTOR", "PATIENT"}


def correction_rediarization_enabled() -> bool:
    """Return whether stopped visits may attempt the rebuild-and-repair leg.

    Operators enable it per deployment; an unset or empty flag keeps every
    clinician visit on today's correction path.

    Returns:
        True only for an explicit enable value; absent, empty, or off-style
        values keep the lane closed.
    """
    configured_value = os.environ.get(REDIARIZATION_FLAG, "").strip().lower()
    return configured_value in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ComparerThresholds:
    """Frozen policy numbers ADR-010 owns; code never redefines them.

    Attributes:
        min_linkage_seconds: Clean-time overlap a rebuilt slot needs before it can
            prove which live voice it is; below it the span keeps live ownership.
        linkage_margin_ratio: Best-vs-second linkage lead required; below it the
            slot straddles two voices and the span keeps live ownership.
        span_match_margin_seconds: Timing slack when matching rows to a span, so
            boundary jitter cannot hide the span's own rows.
    """

    min_linkage_seconds: float = 3.0
    linkage_margin_ratio: float = 2.0
    span_match_margin_seconds: float = 0.25


DEFAULT_THRESHOLDS = ComparerThresholds()


@dataclass(frozen=True)
class SpanDecision:
    """One fold-suspect span's replacement decision for the operator ledger.

    Attributes:
        span_start: Fold span start in visit seconds.
        span_end: Fold span end in visit seconds.
        fold_target_slot: Live chip the span's words were folded into.
        decision: Frozen decision label; every keep_live_* leaves the visit untouched.
        linked_live_slot: Live chip the rebuilt voice linked to; None means no link was proven.
        replacement_role: Role applied via row exceptions; None means no change,
            UNKNOWN means the span routes to the review lane.
        evidence: Count/time-only linkage numbers backing the decision.
    """

    span_start: float
    span_end: float
    fold_target_slot: str
    decision: str
    linked_live_slot: str | None
    replacement_role: str | None
    evidence: dict[str, Any]


def _overlap_seconds(first_start, first_end, second_start, second_end) -> float:
    """Return the shared seconds between two spans; zero means no contact."""
    return max(0.0, min(first_end, second_end) - max(first_start, second_start))


def _is_span_touching(row, span_start, span_end, margin) -> bool:
    """Return whether a row carries any of the span's audio within the margin."""
    return (
        _overlap_seconds(
            float(row["start"]),
            float(row["end"]),
            span_start - margin,
            span_end + margin,
        )
        > 0
    )


def _dominant_rebuilt_slot(rebuilt_rows, span_start, span_end, margin) -> str | None:
    """Return the rebuilt slot owning most of the span's audio, or None for silence."""
    overlap_by_slot: dict[str, float] = {}
    # Every rebuilt row touching the span votes with its overlapping seconds.
    for row in rebuilt_rows:
        seconds = _overlap_seconds(
            float(row["start"]),
            float(row["end"]),
            span_start - margin,
            span_end + margin,
        )
        # Rows outside the span say nothing about who spoke it.
        if seconds <= 0:
            continue
        slot = str(row["speaker_id"])
        overlap_by_slot[slot] = overlap_by_slot.get(slot, 0.0) + seconds

    # No rebuilt voice at the span means the rebuild offers no evidence here.
    if not overlap_by_slot:
        return None
    return max(overlap_by_slot, key=overlap_by_slot.get)


def _linkage_by_live_slot(
    rebuilt_slot_rows, live_rows, fold_spans, margin
) -> dict[str, float]:
    """Sum the rebuilt slot's overlap with each live chip's non-suspect speech.

    Live rows that touch any fold-suspect span are excluded: those are the very
    regions under dispute, so linkage may only use speech both sides agree on.
    """
    linkage: dict[str, float] = {}
    # Each clean live row can anchor the rebuilt voice to one visible chip.
    for live in live_rows:
        # Disputed rows cannot prove identity for the dispute's own resolution.
        if any(
            _is_span_touching(live, s["start_seconds"], s["end_seconds"], margin)
            for s in fold_spans
        ):
            continue
        for rebuilt in rebuilt_slot_rows:
            seconds = _overlap_seconds(
                float(live["start"]),
                float(live["end"]),
                float(rebuilt["start"]),
                float(rebuilt["end"]),
            )
            # Non-touching rows share no audio and add no linkage.
            if seconds <= 0:
                continue
            slot = str(live["speaker_id"])
            linkage[slot] = linkage.get(slot, 0.0) + seconds
    return linkage


def _settled_live_role(live_rows, live_slot) -> str:
    """Return the role the visit settled on for one live chip; UNKNOWN means none."""
    role_counts: dict[str, int] = {}
    # The chip's own labeled rows say what the clinician saw it called.
    for row in live_rows:
        if str(row["speaker_id"]) != live_slot:
            continue
        role = str(row.get("role", "")).upper()
        # Unlabeled rows carry no settled role evidence.
        if role not in SUPPORTED_VISIBLE_ROLES:
            continue
        role_counts[role] = role_counts.get(role, 0) + 1
    if not role_counts:
        return "UNKNOWN"
    return max(role_counts, key=role_counts.get)


def _span_decision(
    span_start: float,
    span_end: float,
    fold_target: str,
    decision: str,
    linked_live_slot: str | None = None,
    replacement_role: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> SpanDecision:
    """Build one span outcome; None evidence means no linkage was measured.

    Returns:
        The assembled decision record for the operator ledger.
    """
    return SpanDecision(
        span_start,
        span_end,
        fold_target,
        decision,
        linked_live_slot,
        replacement_role,
        evidence if evidence is not None else {},
    )


def decide_fold_span(
    fold_span: dict[str, Any],
    live_rows: list[dict[str, Any]],
    rebuilt_rows: list[dict[str, Any]],
    thresholds: ComparerThresholds,
    all_fold_spans: list[dict[str, Any]] | None = None,
) -> SpanDecision:
    """Decide one fold-suspect span with the frozen structural policy.

    Args:
        fold_span: Span timing plus the live chip it was folded into.
        live_rows: Settled live rows; empty means nothing can be replaced.
        rebuilt_rows: Offline-rebuilt rows; empty keeps live ownership.
        thresholds: Frozen ADR numbers; never tuned per call.
        all_fold_spans: Every suspect span for linkage exclusion; None means
            only this span is excluded.

    Returns:
        The span's decision; every non-replace outcome leaves the visit untouched.
    """
    span_start = float(fold_span["start_seconds"])
    span_end = float(fold_span["end_seconds"])
    fold_target = str(fold_span["visible_speaker_slot"])
    exclusion_spans = all_fold_spans if all_fold_spans is not None else [fold_span]
    margin = thresholds.span_match_margin_seconds

    rebuilt_slot = _dominant_rebuilt_slot(rebuilt_rows, span_start, span_end, margin)
    # The rebuild heard nothing here, so the live transcript stands.
    if rebuilt_slot is None:
        return _span_decision(
            span_start,
            span_end,
            fold_target,
            "keep_live_no_rebuilt_evidence",
            evidence={"rebuilt_slot": None},
        )

    rebuilt_slot_rows = [
        r for r in rebuilt_rows if str(r["speaker_id"]) == rebuilt_slot
    ]
    linkage = _linkage_by_live_slot(
        rebuilt_slot_rows, live_rows, exclusion_spans, margin
    )
    evidence = {
        "rebuilt_slot": rebuilt_slot,
        "linkage_seconds_by_live_slot": {k: round(v, 3) for k, v in linkage.items()},
    }

    keep_live_label, linked_live_slot = _linkage_verdict(
        linkage, fold_target, thresholds
    )
    # A failed structural witness keeps live ownership without a second check.
    if keep_live_label is not None:
        return _span_decision(
            span_start,
            span_end,
            fold_target,
            keep_live_label,
            linked_live_slot,
            evidence=evidence,
        )

    return _witness_decision(
        span_start=span_start,
        span_end=span_end,
        fold_target=fold_target,
        linked_live_slot=linked_live_slot,
        rebuilt_slot_rows=rebuilt_slot_rows,
        live_rows=live_rows,
        margin=margin,
        evidence=evidence,
    )


def _linkage_verdict(
    linkage: dict[str, float],
    fold_target: str,
    thresholds: ComparerThresholds,
) -> tuple[str | None, str | None]:
    """Judge the structural witness from the rebuilt slot's clean-time linkage.

    Args:
        linkage: Clean-overlap seconds per live chip; empty proves nothing.
        fold_target: Live chip the span's words were folded into.
        thresholds: Frozen ADR numbers; never tuned per call.

    Returns:
        `(keep_live_label, linked_live_slot)`; a None label means the witness
        proved a cross-chip link and the wording witness must now be heard.
    """
    ranked = sorted(linkage.items(), key=lambda item: -item[1])
    # Too little clean overlap means the voice's identity is unproven.
    if not ranked or ranked[0][1] < thresholds.min_linkage_seconds:
        return "keep_live_insufficient_linkage", None

    # A slot straddling two chips almost evenly must not pick a side.
    if (
        len(ranked) > 1
        and ranked[1][1] > 0
        and (ranked[0][1] / ranked[1][1] < thresholds.linkage_margin_ratio)
    ):
        return "keep_live_ambiguous_linkage", None

    linked_live_slot = ranked[0][0]
    # The same voice churned between cache slots: a benign fold, keep live.
    if linked_live_slot == fold_target:
        return "keep_live_same_voice", linked_live_slot

    return None, linked_live_slot


def _witness_decision(
    *,
    span_start: float,
    span_end: float,
    fold_target: str,
    linked_live_slot: str,
    rebuilt_slot_rows: list[dict[str, Any]],
    live_rows: list[dict[str, Any]],
    margin: float,
    evidence: dict[str, Any],
) -> SpanDecision:
    """Apply the settled-role and wording-witness checks for one linked span.

    Runs only after the structural witness proved a cross-chip link; every
    non-replace outcome leaves the visit untouched.

    Returns:
        The span's final decision under the frozen two-witness policy.
    """
    replacement_role = _settled_live_role(live_rows, linked_live_slot)
    # A different voice whose chip never earned a role goes to review, not a guess.
    if replacement_role == "UNKNOWN":
        return _span_decision(
            span_start,
            span_end,
            fold_target,
            "route_review",
            linked_live_slot,
            "UNKNOWN",
            evidence,
        )

    # A replacement needs a second, independent witness: the wording-based label
    # the role agent gave this rebuilt voice. Impure rebuilt slots can link a
    # locally-correct span to the wrong chip; a conflicting or absent wording
    # witness keeps the clinician's existing label untouched.
    wording_witness_role = _wording_witness_role(
        rebuilt_slot_rows, span_start, span_end, margin
    )
    evidence["wording_witness_role"] = wording_witness_role
    # An unlabeled rebuilt voice cannot vote, so nothing may change.
    if wording_witness_role == "UNKNOWN":
        return _span_decision(
            span_start,
            span_end,
            fold_target,
            "keep_live_no_role_witness",
            linked_live_slot,
            evidence=evidence,
        )
    # Conflicting witnesses mean the span's ownership is disputed; keep live.
    if wording_witness_role != replacement_role:
        return _span_decision(
            span_start,
            span_end,
            fold_target,
            "keep_live_witness_disagreement",
            linked_live_slot,
            evidence=evidence,
        )

    # The clinician already sees this role at the span, so nothing changes.
    span_live_roles = {
        str(row.get("role", "")).upper()
        for row in live_rows
        if _is_span_touching(row, span_start, span_end, margin)
        and str(row.get("role", "")).upper() in SUPPORTED_VISIBLE_ROLES
    }
    if span_live_roles == {replacement_role}:
        return _span_decision(
            span_start,
            span_end,
            fold_target,
            "keep_live_role_agreement",
            linked_live_slot,
            evidence=evidence,
        )

    return _span_decision(
        span_start,
        span_end,
        fold_target,
        "replace_role",
        linked_live_slot,
        replacement_role,
        evidence,
    )


def _wording_witness_role(rebuilt_slot_rows, span_start, span_end, margin) -> str:
    """Return the agent's wording-based label for this rebuilt voice.

    Span-local rows vote first; when the span's own rows are unlabeled, the
    slot's visit-wide labels stand in. UNKNOWN means no witness exists and no
    replacement may proceed.
    """
    span_counts: dict[str, int] = {}
    slot_counts: dict[str, int] = {}
    # Every labeled row of this rebuilt voice can witness its role.
    for row in rebuilt_slot_rows:
        role = str(row.get("role", "")).upper()
        # Unlabeled rows carry no wording evidence.
        if role not in SUPPORTED_VISIBLE_ROLES:
            continue
        slot_counts[role] = slot_counts.get(role, 0) + 1
        # Rows at the span itself are the strongest witness.
        if _is_span_touching(row, span_start, span_end, margin):
            span_counts[role] = span_counts.get(role, 0) + 1

    # The span's own labeled rows outrank the slot-wide majority.
    if span_counts:
        return max(span_counts, key=span_counts.get)
    if slot_counts:
        return max(slot_counts, key=slot_counts.get)
    return "UNKNOWN"


def compare_session(
    fold_spans: list[dict[str, Any]],
    live_rows: list[dict[str, Any]],
    rebuilt_rows: list[dict[str, Any]],
    thresholds: ComparerThresholds,
) -> tuple[list[SpanDecision], dict[str, str]]:
    """Decide every fold-suspect span and build the row-exception payload.

    Args:
        fold_spans: The visit's fold-suspect spans; empty means nothing to decide.
        live_rows: Settled live rows the clinician saw.
        rebuilt_rows: Offline-rebuilt rows for the same audio.
        thresholds: Frozen ADR numbers.

    Returns:
        Span decisions plus {segment_id: role} exceptions; empty exceptions mean
        the visit's corrected transcript is untouched.
    """
    decisions: list[SpanDecision] = []

    # Every suspect span is decided independently under the same frozen numbers.
    for fold_span in fold_spans:
        decisions.append(
            decide_fold_span(
                fold_span, live_rows, rebuilt_rows, thresholds, all_fold_spans=fold_spans
            )
        )

    return decisions, span_role_repairs_for_rows(live_rows, decisions, thresholds)


def span_role_repairs_for_rows(
    rows: list[dict[str, Any]],
    decisions: list[SpanDecision] | tuple[SpanDecision, ...],
    thresholds: ComparerThresholds = DEFAULT_THRESHOLDS,
) -> dict[str, str]:
    """Map span decisions onto one row set's `{segment_id: role}` exceptions.

    Works for live rows and for corrected rows alike, so both artifacts apply
    exactly the same span-to-row matching.

    Args:
        rows: Rows with `segment_id`, timing, and `role`; empty means no repairs.
        decisions: Span outcomes from the frozen policy; empty returns no changes.
        thresholds: Frozen ADR numbers owning the span-match margin.

    Returns:
        `{segment_id: role}` for rows inside a replace/review span whose role
        differs; rows already showing the decided role need no exception.
    """
    row_exceptions: dict[str, str] = {}
    # Only replace/review outcomes touch rows, and only rows inside the span.
    for decision in decisions:
        if decision.replacement_role is None:
            continue
        for row in rows:
            # Rows outside the span keep their ownership untouched.
            if not _is_span_touching(
                row,
                decision.span_start,
                decision.span_end,
                thresholds.span_match_margin_seconds,
            ):
                continue
            # Agreeing rows need no exception; the UI already shows the role.
            if str(row.get("role", "")).upper() == decision.replacement_role:
                continue
            row_exceptions[str(row["segment_id"])] = decision.replacement_role
    return row_exceptions


@dataclass(frozen=True)
class RediarRebuildResult:
    """Outcome of one correction-time rebuild leg for endpoint provenance.

    Attributes:
        status: `completed`, one of the `skipped_*` gates, or `failed`; every
            non-completed status carries no decisions and changes nothing.
        decisions: Per-span policy outcomes for the operator ledger.
        row_exceptions: `{segment_id: role}` for live rows; empty touches nothing.
        rebuilt_row_count: Rebuilt rows the role agent labeled; zero means the
            rebuild produced no usable structure.
        diarization_seconds: Wall time of the Sortformer pass; None when it never ran.
    """

    status: str
    decisions: tuple[SpanDecision, ...] = ()
    row_exceptions: dict[str, str] = field(default_factory=dict)
    rebuilt_row_count: int = 0
    diarization_seconds: float | None = None


def _word_center_seconds(word_timing: dict[str, Any]) -> float:
    """Return one timed word's midpoint for turn containment checks."""
    return (float(word_timing["start"]) + float(word_timing["end"])) / 2.0


def _turn_index_for_word(word_timing: dict[str, Any], turns: list[dict[str, Any]]) -> int | None:
    """Choose the rebuilt turn owning one timed word; None means unassigned.

    Containment wins outright; a gap word may snap to the nearest turn edge
    within the frozen tolerance, and farther words stay honestly unassigned.
    """
    word_center = _word_center_seconds(word_timing)
    nearest_index: int | None = None
    nearest_gap: float | None = None
    # The closest turn adopts the word; zero gap means containment.
    for turn_index, turn in enumerate(turns):
        gap = max(
            float(turn["start"]) - word_center,
            word_center - float(turn["end"]),
            0.0,
        )
        if nearest_gap is None or gap < nearest_gap:
            nearest_gap = gap
            nearest_index = turn_index

    # Words beyond the tolerance window belong to no rebuilt voice.
    if nearest_gap is None or nearest_gap > ASSIGNMENT_TOLERANCE_SECONDS:
        return None
    return nearest_index


def rebuilt_rows_from_word_timings(
    word_timings: list[dict[str, Any]],
    turns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Group the correction's timed words into rebuilt speaker rows.

    Args:
        word_timings: Visit-relative timed words from the second ASR pass;
            empty produces no rows.
        turns: Rebuilt speaker turns from full-audio diarization; empty means
            every word stays unassigned.

    Returns:
        Chronological rebuilt rows with UNKNOWN roles for the wording witness
        to label; empty means the rebuild offers no structure.
    """
    rows: list[dict[str, Any]] = []
    current_turn_index: int | None = None
    current_words: list[dict[str, Any]] = []

    def _flush() -> None:
        # An open word group becomes one rebuilt row under its turn's slot.
        if current_turn_index is None or not current_words:
            return
        rows.append(
            {
                "segment_id": f"rebuilt-{len(rows) + 1:04d}",
                "speaker_id": str(turns[current_turn_index]["speaker_id"]),
                "role": "UNKNOWN",
                "text": " ".join(str(w["word"]) for w in current_words),
                "start": float(current_words[0]["start"]),
                "end": float(current_words[-1]["end"]),
            }
        )

    # Consecutive words in the same turn form one row, preserving spoken order.
    for word_timing in word_timings:
        turn_index = _turn_index_for_word(word_timing, turns)
        # Unassigned words close the current group and are dropped honestly.
        if turn_index is None:
            _flush()
            current_turn_index = None
            current_words = []
            continue
        if turn_index != current_turn_index:
            _flush()
            current_turn_index = turn_index
            current_words = []
        current_words.append(word_timing)

    _flush()
    return rows


def _diarize_speaker_turns(audio_path: str) -> list[dict[str, Any]]:
    """Run full-audio Sortformer once and return rebuilt speaker turns.

    Loads the model, diarizes the retained WAV, and releases the model before
    returning so the correction lane's memory profile stays bounded. Runs on
    the correction executor thread only; the live singleton is never touched.
    """
    import torch
    from nemo.collections.asr.models import SortformerEncLabelModel

    diar_model = (
        SortformerEncLabelModel.from_pretrained(DEFAULT_SORTFORMER_MODEL)
        .eval()
        .to(torch.device("cuda" if torch.cuda.is_available() else "cpu"))
    )
    try:
        with torch.inference_mode():
            diarization_output = diar_model.diarize(
                audio=audio_path,
                batch_size=1,
                verbose=False,
            )
    finally:
        # The rebuild model must not outlive its one pass on the shared GPU.
        del diar_model
        gc.collect()
        try:
            torch.cuda.empty_cache()
        except Exception as cache_error:  # pragma: no cover - reproducing needs a CUDA allocator failure after a completed diarization pass.
            logger.warning(
                "rediar_rebuild.cache_release_failed %s", type(cache_error).__name__
            )

    turns: list[dict[str, Any]] = []
    first_file_segments = (
        diarization_output[0] if isinstance(diarization_output, list) else []
    )
    # Each Sortformer row is `start end speaker_id`; malformed rows are omitted.
    for raw_segment in first_file_segments or []:
        parts = str(raw_segment).strip().split()
        if len(parts) < 3:
            continue
        try:
            turn_start = float(parts[0])
            turn_end = float(parts[1])
        except ValueError:
            continue
        turns.append(
            {
                "speaker_id": parts[2],
                "start": turn_start,
                "end": max(turn_start, turn_end),
            }
        )
    return turns


def _label_rebuilt_rows_with_role_agent(
    rebuilt_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Run one role-agent pass over the rebuilt rows for the wording witness.

    Uses the production payload, evidence builder, tool, and row-exception lane
    under a throwaway session ID; role state never outlives this call. Exactly
    one provider invocation happens per correction.
    """
    from api.role_agent_runtime import run_role_inference
    from api.role_heuristics import compute_row_role_exceptions
    from api.role_inference_queue import _build_bounded_role_evidence
    from tools.assign_roles import cleanup_session

    class _RebuiltRowStore:
        """Serve only the rebuilt rows to the evidence builder."""

        def __init__(self, rows: list[dict[str, Any]]) -> None:
            self._rows = rows

        def get_segments(self, _session_id: str) -> list[dict[str, Any]]:
            """Return the rebuilt rows the evidence builder summarizes.

            Returns:
                The rebuilt rows exactly as constructed; never None.
            """
            return self._rows

    witness_session_id = str(uuid.uuid4())
    try:
        evidence = _build_bounded_role_evidence(
            witness_session_id, _RebuiltRowStore(rebuilt_rows), rebuilt_rows
        )
        inference = run_role_inference(witness_session_id, rebuilt_rows, evidence) or {}
        mapping = dict(inference.get("mapping", {}))
        row_exceptions = compute_row_role_exceptions(rebuilt_rows, mapping)
    finally:
        # Throwaway witness state must never outlive this correction.
        cleanup_session(witness_session_id)

    # Runtime precedence: row exception first, then the slot mapping.
    return [
        {
            **row,
            "role": row_exceptions.get(
                str(row["segment_id"]),
                mapping.get(str(row["speaker_id"]), "UNKNOWN"),
            ),
        }
        for row in rebuilt_rows
    ]


def run_rediar_rebuild_leg(
    *,
    audio_path: str,
    audio_duration_seconds: float,
    word_timings: list[dict[str, Any]] | None,
    live_segments: list[dict[str, Any]],
    fold_spans: list[dict[str, Any]],
    diarize_turns: Callable[[str], list[dict[str, Any]]] | None = None,
    label_rebuilt_rows: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
    | None = None,
) -> RediarRebuildResult:
    """Run the rebuild-and-decide leg for one flag-on correction.

    Args:
        audio_path: The correction's retained-audio WAV, still on disk.
        audio_duration_seconds: Retained audio length; beyond the frozen
            envelope the leg gates off rather than probe unproven capacity.
        word_timings: Visit-relative validated timings; None means no rebuilt
            rows can exist and the leg skips.
        live_segments: Settled live rows the clinician saw.
        fold_spans: The visit's fold-suspect spans; empty means nothing to repair.
        diarize_turns: Test seam for the Sortformer pass; None uses the GPU.
        label_rebuilt_rows: Test seam for the wording witness; None runs the
            one production role-agent pass.

    Returns:
        The leg's outcome; any internal failure returns `failed` with no
        exceptions so the corrected transcript is never lost to this lane.
    """
    # A visit that never folded has nothing this policy may touch.
    if not fold_spans:
        return RediarRebuildResult(status="skipped_no_fold_spans")
    # Without timed words the rebuild cannot form rows for either witness.
    if not word_timings:
        return RediarRebuildResult(status="skipped_no_word_timings")
    # Beyond the M02-proven envelope the lane gates by visit length.
    if audio_duration_seconds > REDIAR_MAX_AUDIO_SECONDS:
        return RediarRebuildResult(status="skipped_envelope")

    try:
        diarize = diarize_turns or _diarize_speaker_turns
        diarization_started = time.monotonic()
        turns = diarize(audio_path)
        diarization_seconds = time.monotonic() - diarization_started

        rebuilt_rows = rebuilt_rows_from_word_timings(word_timings, turns)
        # No rebuilt structure still yields honest keep-live decisions per span.
        labeler = label_rebuilt_rows or _label_rebuilt_rows_with_role_agent
        labeled_rows = labeler(rebuilt_rows) if rebuilt_rows else []

        decisions, row_exceptions = compare_session(
            fold_spans, live_segments, labeled_rows, DEFAULT_THRESHOLDS
        )
        return RediarRebuildResult(
            status="completed",
            decisions=tuple(decisions),
            row_exceptions=row_exceptions,
            rebuilt_row_count=len(rebuilt_rows),
            diarization_seconds=round(diarization_seconds, 3),
        )
    except Exception as rebuild_error:
        # Example: Sortformer fails to load after a healthy correction; the
        # clinician keeps the corrected transcript and only the repair is lost.
        logger.warning(
            "correction.rediarization_failed error_type=%s",
            type(rebuild_error).__name__,
            extra={"error_type": type(rebuild_error).__name__},
        )
        return RediarRebuildResult(status="failed")
