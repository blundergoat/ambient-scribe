"""Decision-table contracts for the fold-span replacement comparer.

The comparer decides, per fold-suspect span, whether the corrected transcript
keeps the live speaker ownership or takes a row-level role exception sourced
from the offline rebuild's slot structure. These tests pin every decision
branch with synthetic sessions so the policy cannot drift silently; the
specimen grading against TextGrid truth happens in the offline QA run, not here.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location(
    "rediar_span_comparer", REPO_ROOT / "scripts/rediar-span-comparer.py"
)
comparer = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = comparer
_spec.loader.exec_module(comparer)


def live_row(segment_id, speaker_id, role, start, end):
    """One settled live transcript row as the correction path sees it."""
    return {
        "segment_id": segment_id,
        "speaker_id": speaker_id,
        "role": role,
        "start": start,
        "end": end,
        "text": "",
    }


def rebuilt_row(speaker_id, start, end, role="UNKNOWN"):
    """One offline-rebuilt row: slot and timing plus the agent's wording-based label."""
    return {
        "speaker_id": speaker_id,
        "start": start,
        "end": end,
        "role": role,
        "text": "",
    }


DEFAULT_THRESHOLDS = (
    None  # populated after module load in each test via comparer.DEFAULT_THRESHOLDS
)


class TestFoldSpanDecisions:
    """Each branch of the frozen decision table, one synthetic case each."""

    def test_different_voice_linkage_replaces_with_linked_live_role(self) -> None:
        """A real other-voice turn folded into the wrong chip gets that voice's own role."""
        # The visit: doctor (speaker_0/DOCTOR) and patient (speaker_1/PATIENT)
        # each own clean non-suspect speech; the fold span sits on the patient chip.
        live_rows = [
            live_row("seg-0001", "speaker_0", "DOCTOR", 0.0, 20.0),
            live_row("seg-0002", "speaker_1", "PATIENT", 20.0, 40.0),
            live_row("seg-0003", "speaker_1", "PATIENT", 66.0, 67.0),
        ]
        # The rebuild hears the span's words in a slot that elsewhere overlaps
        # the DOCTOR chip's clean speech - a different voice than the fold target -
        # and the wording witness independently agrees the voice is the doctor's.
        rebuilt_rows = [
            rebuilt_row("r0", 0.0, 20.0, role="DOCTOR"),
            rebuilt_row("r0", 66.0, 67.0, role="DOCTOR"),
        ]
        decision = comparer.decide_fold_span(
            {
                "start_seconds": 66.2,
                "end_seconds": 66.8,
                "visible_speaker_slot": "speaker_1",
            },
            live_rows,
            rebuilt_rows,
            comparer.DEFAULT_THRESHOLDS,
        )

        assert decision.decision == "replace_role"
        assert decision.linked_live_slot == "speaker_0"
        assert decision.replacement_role == "DOCTOR"

    def test_same_voice_linkage_keeps_live(self) -> None:
        """Early-session churn folds of the same voice never override the live chip."""
        live_rows = [
            live_row("seg-0001", "speaker_0", "DOCTOR", 0.0, 20.0),
            live_row("seg-0002", "speaker_0", "DOCTOR", 25.0, 26.0),
        ]
        # The rebuild's slot at the span spends its clean time on the SAME chip
        # the fold targeted, so this is one voice moving between cache slots.
        rebuilt_rows = [
            rebuilt_row("r2", 0.0, 20.0),
            rebuilt_row("r2", 25.0, 26.0),
        ]
        decision = comparer.decide_fold_span(
            {
                "start_seconds": 25.2,
                "end_seconds": 25.8,
                "visible_speaker_slot": "speaker_0",
            },
            live_rows,
            rebuilt_rows,
            comparer.DEFAULT_THRESHOLDS,
        )

        assert decision.decision == "keep_live_same_voice"
        assert decision.replacement_role is None

    def test_no_rebuilt_rows_keeps_live(self) -> None:
        """Without rebuild evidence at the span, the live transcript stands."""
        live_rows = [live_row("seg-0001", "speaker_0", "DOCTOR", 0.0, 30.0)]
        decision = comparer.decide_fold_span(
            {
                "start_seconds": 10.0,
                "end_seconds": 10.5,
                "visible_speaker_slot": "speaker_0",
            },
            live_rows,
            [],
            comparer.DEFAULT_THRESHOLDS,
        )

        assert decision.decision == "keep_live_no_rebuilt_evidence"

    def test_insufficient_linkage_seconds_keeps_live(self) -> None:
        """A rebuilt slot with almost no clean overlap cannot prove which voice it is."""
        live_rows = [
            live_row("seg-0001", "speaker_0", "DOCTOR", 0.0, 1.0),
            live_row("seg-0002", "speaker_1", "PATIENT", 20.0, 21.0),
        ]
        # The slot's only clean-time overlap is far below the evidence floor.
        rebuilt_rows = [
            rebuilt_row("r0", 0.0, 1.0),
            rebuilt_row("r0", 20.5, 21.5),
        ]
        decision = comparer.decide_fold_span(
            {
                "start_seconds": 20.6,
                "end_seconds": 21.0,
                "visible_speaker_slot": "speaker_1",
            },
            live_rows,
            rebuilt_rows,
            comparer.DEFAULT_THRESHOLDS,
        )

        assert decision.decision == "keep_live_insufficient_linkage"

    def test_ambiguous_linkage_margin_keeps_live(self) -> None:
        """A slot overlapping two chips almost equally must not pick a side."""
        live_rows = [
            live_row("seg-0001", "speaker_0", "DOCTOR", 0.0, 10.0),
            live_row("seg-0002", "speaker_1", "PATIENT", 10.0, 19.0),
            live_row("seg-0003", "speaker_1", "PATIENT", 30.0, 31.0),
        ]
        # The rebuilt slot straddles both chips' clean speech nearly evenly.
        rebuilt_rows = [
            rebuilt_row("r0", 5.0, 15.0),
            rebuilt_row("r0", 30.0, 31.0),
        ]
        decision = comparer.decide_fold_span(
            {
                "start_seconds": 30.2,
                "end_seconds": 30.8,
                "visible_speaker_slot": "speaker_1",
            },
            live_rows,
            rebuilt_rows,
            comparer.DEFAULT_THRESHOLDS,
        )

        assert decision.decision == "keep_live_ambiguous_linkage"

    def test_linked_slot_without_settled_role_routes_review(self) -> None:
        """A different voice whose chip never earned a role goes to review, not a guess."""
        live_rows = [
            live_row("seg-0001", "speaker_2", "UNKNOWN", 0.0, 20.0),
            live_row("seg-0002", "speaker_1", "PATIENT", 40.0, 41.0),
        ]
        rebuilt_rows = [
            rebuilt_row("r0", 0.0, 20.0),
            rebuilt_row("r0", 40.0, 41.0),
        ]
        decision = comparer.decide_fold_span(
            {
                "start_seconds": 40.2,
                "end_seconds": 40.8,
                "visible_speaker_slot": "speaker_1",
            },
            live_rows,
            rebuilt_rows,
            comparer.DEFAULT_THRESHOLDS,
        )

        assert decision.decision == "route_review"
        assert decision.replacement_role == "UNKNOWN"

    def test_witness_disagreement_keeps_live(self) -> None:
        """When structure says replace but the wording witness disagrees, nothing moves.

        This is the impure-slot guard: a span whose audio locally belongs to the
        live-correct voice can sit inside a rebuilt slot majority-linked elsewhere;
        the wording witness catches it and the clinician's correct label survives.
        """
        live_rows = [
            live_row("seg-0001", "speaker_0", "DOCTOR", 0.0, 20.0),
            live_row("seg-0002", "speaker_1", "PATIENT", 20.0, 40.0),
            live_row("seg-0003", "speaker_1", "PATIENT", 66.0, 67.0),
        ]
        # Structure links r0 to the DOCTOR chip, but the wording witness read the
        # slot's speech as PATIENT - the two witnesses conflict.
        rebuilt_rows = [
            rebuilt_row("r0", 0.0, 20.0, role="PATIENT"),
            rebuilt_row("r0", 66.0, 67.0, role="PATIENT"),
        ]
        decision = comparer.decide_fold_span(
            {
                "start_seconds": 66.2,
                "end_seconds": 66.8,
                "visible_speaker_slot": "speaker_1",
            },
            live_rows,
            rebuilt_rows,
            comparer.DEFAULT_THRESHOLDS,
        )

        assert decision.decision == "keep_live_witness_disagreement"
        assert decision.replacement_role is None

    def test_absent_wording_witness_keeps_live(self) -> None:
        """A replacement needs both witnesses; an unlabeled rebuilt slot cannot vote."""
        live_rows = [
            live_row("seg-0001", "speaker_0", "DOCTOR", 0.0, 20.0),
            live_row("seg-0002", "speaker_1", "PATIENT", 20.0, 40.0),
            live_row("seg-0003", "speaker_1", "PATIENT", 66.0, 67.0),
        ]
        rebuilt_rows = [
            rebuilt_row("r0", 0.0, 20.0),
            rebuilt_row("r0", 66.0, 67.0),
        ]
        decision = comparer.decide_fold_span(
            {
                "start_seconds": 66.2,
                "end_seconds": 66.8,
                "visible_speaker_slot": "speaker_1",
            },
            live_rows,
            rebuilt_rows,
            comparer.DEFAULT_THRESHOLDS,
        )

        assert decision.decision == "keep_live_no_role_witness"
        assert decision.replacement_role is None

    def test_agreeing_role_is_a_no_op_keep(self) -> None:
        """A different-voice link whose role matches the visible label changes nothing.

        The clinician already sees the right role, so the decision reads keep,
        not replace - the control fixture must show pure keep decisions.
        """
        live_rows = [
            live_row("seg-0001", "speaker_0", "DOCTOR", 0.0, 20.0),
            live_row("seg-0002", "speaker_1", "PATIENT", 20.0, 40.0),
            live_row("seg-0003", "speaker_2", "DOCTOR", 66.0, 67.0),
        ]
        # Structure and wording both say DOCTOR - which the row already shows.
        rebuilt_rows = [
            rebuilt_row("r0", 0.0, 20.0, role="DOCTOR"),
            rebuilt_row("r0", 66.0, 67.0, role="DOCTOR"),
        ]
        decision = comparer.decide_fold_span(
            {
                "start_seconds": 66.2,
                "end_seconds": 66.8,
                "visible_speaker_slot": "speaker_2",
            },
            live_rows,
            rebuilt_rows,
            comparer.DEFAULT_THRESHOLDS,
        )

        assert decision.decision == "keep_live_role_agreement"
        assert decision.replacement_role is None

    def test_partial_span_overlap_still_counts_by_seconds(self) -> None:
        """A rebuilt row straddling the span boundary is still the span's evidence."""
        live_rows = [
            live_row("seg-0001", "speaker_0", "DOCTOR", 0.0, 20.0),
            live_row("seg-0002", "speaker_1", "PATIENT", 60.0, 61.0),
        ]
        # The rebuilt row starts before the span and ends inside it.
        rebuilt_rows = [
            rebuilt_row("r0", 0.0, 20.0, role="DOCTOR"),
            rebuilt_row("r0", 59.5, 60.6, role="DOCTOR"),
        ]
        decision = comparer.decide_fold_span(
            {
                "start_seconds": 60.2,
                "end_seconds": 60.9,
                "visible_speaker_slot": "speaker_1",
            },
            live_rows,
            rebuilt_rows,
            comparer.DEFAULT_THRESHOLDS,
        )

        assert decision.decision == "replace_role"
        assert decision.linked_live_slot == "speaker_0"


class TestSessionComparison:
    """Whole-session behavior: exceptions payload and the clean-control guarantee."""

    def test_row_exceptions_cover_only_disagreeing_span_rows(self) -> None:
        """Only live rows inside a replaced span whose role differs get exceptions."""
        live_rows = [
            live_row("seg-0001", "speaker_0", "DOCTOR", 0.0, 20.0),
            live_row("seg-0002", "speaker_1", "PATIENT", 20.0, 40.0),
            live_row("seg-0003", "speaker_1", "PATIENT", 66.0, 67.0),
        ]
        rebuilt_rows = [
            rebuilt_row("r0", 0.0, 20.0, role="DOCTOR"),
            rebuilt_row("r0", 66.0, 67.0, role="DOCTOR"),
        ]
        decisions, row_exceptions = comparer.compare_session(
            [
                {
                    "start_seconds": 66.2,
                    "end_seconds": 66.8,
                    "visible_speaker_slot": "speaker_1",
                }
            ],
            live_rows,
            rebuilt_rows,
            comparer.DEFAULT_THRESHOLDS,
        )

        assert [d.decision for d in decisions] == ["replace_role"]
        assert row_exceptions == {"seg-0003": "DOCTOR"}

    def test_same_voice_session_emits_no_exceptions(self) -> None:
        """A churn-only visit (the clean-control shape) leaves the transcript untouched."""
        live_rows = [
            live_row("seg-0001", "speaker_0", "DOCTOR", 0.0, 20.0),
            live_row("seg-0002", "speaker_0", "DOCTOR", 25.0, 26.0),
            live_row("seg-0003", "speaker_1", "PATIENT", 30.0, 50.0),
            live_row("seg-0004", "speaker_1", "PATIENT", 55.0, 56.0),
        ]
        rebuilt_rows = [
            rebuilt_row("r2", 0.0, 20.0),
            rebuilt_row("r2", 25.0, 26.0),
            rebuilt_row("r5", 30.0, 50.0),
            rebuilt_row("r5", 55.0, 56.0),
        ]
        decisions, row_exceptions = comparer.compare_session(
            [
                {
                    "start_seconds": 25.2,
                    "end_seconds": 25.8,
                    "visible_speaker_slot": "speaker_0",
                },
                {
                    "start_seconds": 55.2,
                    "end_seconds": 55.8,
                    "visible_speaker_slot": "speaker_1",
                },
            ],
            live_rows,
            rebuilt_rows,
            comparer.DEFAULT_THRESHOLDS,
        )

        assert {d.decision for d in decisions} == {"keep_live_same_voice"}
        assert row_exceptions == {}
