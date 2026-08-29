"""Choose whether each suspected fold keeps its live role or uses rebuilt ownership.

Use after a stopped visit has rebuilt audio and recorded fold spans. The CLI links rebuilt slots to settled speaker chips without reference truth.
Clean visits keep their visible roles; supported folds can add a row-level role exception.
The policy stays in `strands_agents/rediar_rebuild.py`; this CLI writes the QA ledger without duplicating ADR-013.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Offline QA runs from the repo root, where the runtime package is a sibling.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "strands_agents"))

from rediar_rebuild import (  # noqa: E402 - runtime module path must precede this offline QA import.
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
