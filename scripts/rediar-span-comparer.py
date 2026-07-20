"""Decide, per fold-suspect span, whether the corrected transcript keeps live
speaker ownership or takes a row-level role exception from the offline rebuild.

Use this after a stopped visit's full-audio rebuild exists and the live session
recorded which spans were folded into another speaker's chip. The decision is
structural and truth-free: the rebuild says WHO spoke at the span (its slot's
clean-time overlap links it to a live chip), and the replacement role is that
linked chip's own settled role - never a model label and never reference truth.
A clean visit whose folds were one voice's cache churn decides keep-live
everywhere, so the clinician's correct transcript is never touched.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SUPPORTED_VISIBLE_ROLES = {"DOCTOR", "PATIENT"}


@dataclass(frozen=True)
class ComparerThresholds:
    """Frozen policy numbers the proposed ADR owns; code never redefines them.

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


def _span_touches(row, span_start, span_end, margin) -> bool:
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
            _span_touches(live, s["start_seconds"], s["end_seconds"], margin)
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
        return SpanDecision(
            span_start,
            span_end,
            fold_target,
            "keep_live_no_rebuilt_evidence",
            None,
            None,
            {"rebuilt_slot": None},
        )

    rebuilt_slot_rows = [
        r for r in rebuilt_rows if str(r["speaker_id"]) == rebuilt_slot
    ]
    linkage = _linkage_by_live_slot(
        rebuilt_slot_rows, live_rows, exclusion_spans, margin
    )
    ranked = sorted(linkage.items(), key=lambda item: -item[1])
    evidence = {
        "rebuilt_slot": rebuilt_slot,
        "linkage_seconds_by_live_slot": {k: round(v, 3) for k, v in linkage.items()},
    }

    # Too little clean overlap means the voice's identity is unproven.
    if not ranked or ranked[0][1] < thresholds.min_linkage_seconds:
        return SpanDecision(
            span_start,
            span_end,
            fold_target,
            "keep_live_insufficient_linkage",
            None,
            None,
            evidence,
        )

    # A slot straddling two chips almost evenly must not pick a side.
    if (
        len(ranked) > 1
        and ranked[1][1] > 0
        and (ranked[0][1] / ranked[1][1] < thresholds.linkage_margin_ratio)
    ):
        return SpanDecision(
            span_start,
            span_end,
            fold_target,
            "keep_live_ambiguous_linkage",
            None,
            None,
            evidence,
        )

    linked_live_slot = ranked[0][0]
    # The same voice churned between cache slots: a benign fold, keep live.
    if linked_live_slot == fold_target:
        return SpanDecision(
            span_start,
            span_end,
            fold_target,
            "keep_live_same_voice",
            linked_live_slot,
            None,
            evidence,
        )

    replacement_role = _settled_live_role(live_rows, linked_live_slot)
    # A different voice whose chip never earned a role goes to review, not a guess.
    if replacement_role == "UNKNOWN":
        return SpanDecision(
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
        return SpanDecision(
            span_start,
            span_end,
            fold_target,
            "keep_live_no_role_witness",
            linked_live_slot,
            None,
            evidence,
        )
    # Conflicting witnesses mean the span's ownership is disputed; keep live.
    if wording_witness_role != replacement_role:
        return SpanDecision(
            span_start,
            span_end,
            fold_target,
            "keep_live_witness_disagreement",
            linked_live_slot,
            None,
            evidence,
        )

    # The clinician already sees this role at the span, so nothing changes.
    span_live_roles = {
        str(row.get("role", "")).upper()
        for row in live_rows
        if _span_touches(row, span_start, span_end, margin)
        and str(row.get("role", "")).upper() in SUPPORTED_VISIBLE_ROLES
    }
    if span_live_roles == {replacement_role}:
        return SpanDecision(
            span_start,
            span_end,
            fold_target,
            "keep_live_role_agreement",
            linked_live_slot,
            None,
            evidence,
        )

    return SpanDecision(
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
        if _span_touches(row, span_start, span_end, margin):
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
    row_exceptions: dict[str, str] = {}

    # Every suspect span is decided independently under the same frozen numbers.
    for fold_span in fold_spans:
        decision = decide_fold_span(
            fold_span, live_rows, rebuilt_rows, thresholds, all_fold_spans=fold_spans
        )
        decisions.append(decision)
        # Only replace/review outcomes touch rows, and only rows inside the span.
        if decision.replacement_role is None:
            continue
        for row in live_rows:
            # Rows outside the span keep their ownership untouched.
            if not _span_touches(
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

    return decisions, row_exceptions


def main() -> int:
    """Run the comparer over one session's retained artifacts for QA review.

    Returns:
        Zero after writing one JSON ledger; inputs are developer-selected files.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-history", type=Path, required=True)
    parser.add_argument("--rebuilt-history", type=Path, required=True)
    parser.add_argument("--fold-spans", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    live_rows = json.loads(args.live_history.read_text(encoding="utf-8"))["segments"]
    rebuilt_rows = json.loads(args.rebuilt_history.read_text(encoding="utf-8"))[
        "segments"
    ]
    fold_spans = json.loads(args.fold_spans.read_text(encoding="utf-8"))

    decisions, row_exceptions = compare_session(
        fold_spans, live_rows, rebuilt_rows, DEFAULT_THRESHOLDS
    )
    ledger = {
        "thresholds": {
            "min_linkage_seconds": DEFAULT_THRESHOLDS.min_linkage_seconds,
            "linkage_margin_ratio": DEFAULT_THRESHOLDS.linkage_margin_ratio,
            "span_match_margin_seconds": DEFAULT_THRESHOLDS.span_match_margin_seconds,
        },
        "decisions": [
            {
                "span_start": d.span_start,
                "span_end": d.span_end,
                "fold_target_slot": d.fold_target_slot,
                "decision": d.decision,
                "linked_live_slot": d.linked_live_slot,
                "replacement_role": d.replacement_role,
                "evidence": d.evidence,
            }
            for d in decisions
        ],
        "row_exceptions": row_exceptions,
    }
    args.output.write_text(
        json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        f"rediar-span-comparer spans={len(decisions)} "
        f"replacements={sum(1 for d in decisions if d.decision == 'replace_role')} "
        f"reviews={sum(1 for d in decisions if d.decision == 'route_review')} "
        f"row_exceptions={len(row_exceptions)} output={args.output}"
    )
    return 0


# Direct execution writes one QA ledger; runtime wiring is a later milestone.
if __name__ == "__main__":
    raise SystemExit(main())
