"""Decide, per fold-suspect span, whether the corrected transcript keeps live
speaker ownership or takes a row-level role exception from the offline rebuild.

Use this after a stopped visit's full-audio rebuild exists and the live session
recorded which spans were folded into another speaker's chip. The decision is
structural and truth-free: the rebuild says WHO spoke at the span (its slot's
clean-time overlap links it to a live chip), and the replacement role is that
linked chip's own settled role - never a model label and never reference truth.
A clean visit whose folds were one voice's cache churn decides keep-live
everywhere, so the clinician's correct transcript is never touched.

The policy implementation lives in `strands_agents/rediar_rebuild.py` (the
runtime home since M04); this CLI delegates so the frozen ADR-010 decision
table exists in exactly one place.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Offline QA runs from the repo root, where the runtime package is a sibling.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "strands_agents"))

from rediar_rebuild import (  # noqa: E402
    SUPPORTED_VISIBLE_ROLES,
    ComparerThresholds,
    DEFAULT_THRESHOLDS,
    SpanDecision,
    compare_session,
    decide_fold_span,
)

__all__ = [
    "SUPPORTED_VISIBLE_ROLES",
    "ComparerThresholds",
    "DEFAULT_THRESHOLDS",
    "SpanDecision",
    "compare_session",
    "decide_fold_span",
]


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


# Direct execution writes one QA ledger; the runtime lane lives in rediar_rebuild.
if __name__ == "__main__":
    raise SystemExit(main())
