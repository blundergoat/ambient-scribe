"""Score visible speaker-fold outcomes against Doctor/Patient TextGrid truth.

Use this after an instrumented replay when wording was aliased from one NeMo
cache slot into a visible transcript identity. The report distinguishes a
correct same-role containment, a confidently wrong cross-speaker fold, and an
unresolved outcome without retaining any consultation wording.
"""

from __future__ import annotations

import argparse
from collections import Counter
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_VISIBLE_ROLES = {"DOCTOR", "PATIENT"}


def load_transcript_quality_module() -> ModuleType:
    """Load the shipped scorer so fold acceptance shares its TextGrid semantics.

    Returns:
        Loaded scorer module; absence means the repository scorer cannot be used.

    Raises:
        RuntimeError: The operator cannot score a replay because the shared scorer has no loader.
    """
    module_path = REPO_ROOT / "scripts/transcript-quality.py"
    module_specification = importlib.util.spec_from_file_location(
        "fold_attribution_transcript_quality",
        module_path,
    )

    # A missing loader means this acceptance report cannot use the shipped oracle.
    if module_specification is None or module_specification.loader is None:
        raise RuntimeError(f"Cannot load transcript scorer: {module_path}")

    scorer_module = importlib.util.module_from_spec(module_specification)
    # Dataclass evaluation needs the module registered before its code executes.
    sys.modules[module_specification.name] = scorer_module
    module_specification.loader.exec_module(scorer_module)
    return scorer_module


def load_json_object(path: Path) -> dict[str, Any]:
    """Read one retained JSON object used by the operator acceptance report.

    Args:
        path: Artifact path; an empty or non-object file is invalid evidence.

    Returns:
        Parsed object; an empty object means the artifact explicitly contained `{}`.

    Raises:
        ValueError: The selected replay artifact contains a list or scalar instead of an object.
    """
    parsed_value = json.loads(path.read_text(encoding="utf-8"))

    # Lists and scalar JSON cannot represent one replay history artifact.
    if not isinstance(parsed_value, dict):
        raise ValueError(f"Expected one JSON object: {path}")

    return parsed_value


def load_continuity_windows(path: Path) -> list[dict[str, Any]]:
    """Read instrumented replay windows while excluding the trailing summary.

    Args:
        path: JSONL continuity artifact; empty input means no windows were captured.

    Returns:
        Window objects in log order; empty means the replay logged no fold evidence.

    Raises:
        ValueError: A captured log row is not an object and cannot identify a user-visible fold.
    """
    window_rows: list[dict[str, Any]] = []

    # Each non-empty line is either one clinician-visible window or its final summary.
    for line in path.read_text(encoding="utf-8").splitlines():
        # Blank Docker-log lines carry no speaker decision.
        if line.strip() == "":
            continue

        parsed_row = json.loads(line)
        # Malformed row shapes cannot safely participate in the acceptance count.
        if not isinstance(parsed_row, dict):
            raise ValueError(f"Expected JSONL objects: {path}")

        # The summary repeats counts but has no word-timed fold to classify.
        if parsed_row.get("event") == "window_continuity.summary":
            continue

        window_rows.append(parsed_row)

    return window_rows


def reference_intervals(
    scorer_module: ModuleType,
    textgrid_paths: list[Path],
) -> list[Any]:
    """Return the shared scorer's Doctor/Patient speech intervals.

    Args:
        scorer_module: Loaded transcript-quality module; never absent after startup.
        textgrid_paths: Role-qualified channels; an empty list yields no oracle speech.

    Returns:
        Timed reference intervals; empty means neither role has audible wording.
    """
    intervals: list[Any] = []

    # Both role channels are needed to detect ambiguous simultaneous speech.
    for textgrid_path in textgrid_paths:
        intervals.extend(scorer_module.textgrid_intervals(textgrid_path, float("inf")))

    return intervals


def expected_role_for_fold(
    scorer_module: ModuleType,
    start_seconds: float,
    end_seconds: float,
    intervals: list[Any],
) -> str | None:
    """Return the majority TextGrid role for one folded wording span.

    Args:
        scorer_module: Shared transcript scorer; never absent after startup.
        start_seconds: Folded wording start; zero means the visit began immediately.
        end_seconds: Folded wording end; it must follow the start to overlap speech.
        intervals: Doctor/Patient truth spans; empty means the owner cannot be known.

    Returns:
        DOCTOR/PATIENT, or None when silence/overlap makes the user-visible owner unclear.
    """
    diagnostic_segment = scorer_module.HypothesisSegment(
        start=start_seconds,
        end=end_seconds,
        speaker_id="diagnostic-origin",
        role="UNKNOWN",
        text="",
    )
    return scorer_module.reference_role_for_segment(diagnostic_segment, intervals)


def visible_roles_for_fold(
    scorer_module: ModuleType,
    visible_segments: list[Any],
    visible_speaker_slot: str | None,
    start_seconds: float,
    end_seconds: float,
) -> list[str]:
    """Return confident roles shown under the fold target at the same time.

    Args:
        scorer_module: Shared scorer providing the exact overlap calculation.
        visible_segments: Settled user-visible rows; empty means no role can be observed.
        visible_speaker_slot: Alias target chip, or None to inspect every baseline chip.
        start_seconds: Start of the wording moved into the target chip.
        end_seconds: End of the wording moved into the target chip.

    Returns:
        Sorted known roles; empty means the relevant UI history was absent or UNKNOWN.
    """
    matching_roles: set[str] = set()

    # Settled history tells which confident role the clinician saw for this target identity.
    for segment in visible_segments:
        # Candidate folds use one target chip; Phase 0 may show the person on another chip.
        if (
            visible_speaker_slot is not None
            and segment.speaker_id != visible_speaker_slot
        ):
            continue

        # A row outside the folded timing span did not contain this wording.
        if (
            scorer_module.overlap_seconds(
                segment.start,
                segment.end,
                start_seconds,
                end_seconds,
            )
            <= 0
        ):
            continue

        # UNKNOWN is honest uncertainty, not a confidently wrong attribution.
        if segment.role not in SUPPORTED_VISIBLE_ROLES:
            continue

        matching_roles.add(segment.role)

    return sorted(matching_roles)


def classify_fold(expected_role: str | None, visible_roles: list[str]) -> str:
    """Name whether one alias helps, harms, or leaves ownership unresolved.

    Args:
        expected_role: TextGrid owner; None means silence or simultaneous speech.
        visible_roles: Confident roles on the target chip; empty means UNKNOWN/absent.

    Returns:
        Stable classification label used by the fold acceptance gate.
    """
    # Silence, overlap, or an UNKNOWN visible role cannot prove the alias right or wrong.
    if expected_role is None or visible_roles == []:
        return "unresolved_fold"

    # A matching visible role means the alias contained a duplicate under the correct chip.
    if expected_role in visible_roles:
        return "benign_same_role_fold"

    return "harmful_cross_speaker_fold"


def attribution_state(expected_role: str | None, visible_roles: list[str]) -> str:
    """Reduce one UI span to correct, unresolved, or confidently wrong.

    Args:
        expected_role: TextGrid owner; None means the reference owner is unclear.
        visible_roles: Known UI roles; empty or conflicting roles mean unresolved.

    Returns:
        Ranked attribution state used to compare Phase 0 with the candidate replay.
    """
    # Silence, overlap, no role, or conflicting roles cannot support a confident label.
    if expected_role is None or len(visible_roles) != 1:
        return "unresolved"

    # One matching UI role shows the user the TextGrid-grounded speaker.
    if visible_roles[0] == expected_role:
        return "correct"

    return "wrong"


def compare_attribution_states(baseline_state: str, candidate_state: str) -> str:
    """Name how the candidate changed a user's same-span attribution.

    Args:
        baseline_state: Phase 0 state; never empty after baseline scoring.
        candidate_state: Guarded replay state; never empty after candidate scoring.

    Returns:
        Improved, worsened, or unchanged label for the release gate.
    """
    state_rank = {"wrong": 0, "unresolved": 1, "correct": 2}

    # A higher-ranked candidate gives the user more accurate speaker ownership.
    if state_rank[candidate_state] > state_rank[baseline_state]:
        return "improved"

    # A lower-ranked candidate removes or damages speaker ownership the user had.
    if state_rank[candidate_state] < state_rank[baseline_state]:
        return "worsened"

    return f"unchanged_{candidate_state}"


def add_attribution_deltas(
    scorer_module: ModuleType,
    fold_events: list[dict[str, Any]],
    baseline_segments: list[Any],
) -> None:
    """Annotate candidate folds with Phase 0 user-visible attribution changes.

    Args:
        scorer_module: Shared scorer used for exact timing overlap.
        fold_events: Candidate fold rows; empty means there is nothing to compare.
        baseline_segments: Phase 0 UI rows; empty leaves baseline spans unresolved.
    """
    # Every candidate fold is compared at the same time, independent of cache-slot names.
    for fold_event in fold_events:
        baseline_visible_roles = visible_roles_for_fold(
            scorer_module,
            baseline_segments,
            None,
            float(fold_event["start_seconds"]),
            float(fold_event["end_seconds"]),
        )
        baseline_state = attribution_state(
            fold_event["expected_role"],
            baseline_visible_roles,
        )
        candidate_state = attribution_state(
            fold_event["expected_role"],
            fold_event["visible_roles"],
        )
        fold_event.update(
            {
                "baseline_visible_roles": baseline_visible_roles,
                "baseline_attribution": baseline_state,
                "candidate_attribution": candidate_state,
                "attribution_delta": compare_attribution_states(
                    baseline_state,
                    candidate_state,
                ),
            }
        )


def summarize_attribution_deltas(
    fold_events: list[dict[str, Any]],
) -> dict[str, int]:
    """Count unique same-span attribution changes for the fold release gate.

    Args:
        fold_events: Delta-annotated rows; empty means the candidate made no folds.

    Returns:
        Stable gate counts; every value is zero when there are no candidate folds.
    """
    # Decoder revisions repeat spans, so the last logged copy represents each unique fold.
    unique_events = {
        (
            str(fold_event["origin_speaker_slot"]),
            str(fold_event["visible_speaker_slot"]),
            float(fold_event["start_seconds"]),
            float(fold_event["end_seconds"]),
        ): fold_event
        for fold_event in fold_events
    }
    delta_counts = Counter(
        fold_event["attribution_delta"] for fold_event in unique_events.values()
    )
    # A newly confident wrong result is worse than Phase 0 uncertainty or correctness.
    newly_confident_wrong = sum(
        fold_event["candidate_attribution"] == "wrong"
        and fold_event["baseline_attribution"] != "wrong"
        for fold_event in unique_events.values()
    )
    return {
        "unique_compared_spans": len(unique_events),
        "improved": delta_counts["improved"],
        "worsened": delta_counts["worsened"],
        "unchanged_correct": delta_counts["unchanged_correct"],
        "unchanged_wrong": delta_counts["unchanged_wrong"],
        "unchanged_unresolved": delta_counts["unchanged_unresolved"],
        "newly_confident_wrong": newly_confident_wrong,
    }


def score_fold_events(
    scorer_module: ModuleType,
    continuity_windows: list[dict[str, Any]],
    visible_segments: list[Any],
    intervals: list[Any],
) -> list[dict[str, Any]]:
    """Classify every recorded fold without copying transcript or TextGrid wording.

    Args:
        scorer_module: Shared scorer used for TextGrid parsing and timing overlap.
        continuity_windows: Instrumented windows; empty means no fold was recorded.
        visible_segments: Settled transcript rows; empty leaves every fold unresolved.
        intervals: Doctor/Patient truth spans; empty leaves every fold unresolved.

    Returns:
        PHI-safe fold rows; empty means the replay made no instrumented aliases.
    """
    scored_events: list[dict[str, Any]] = []

    # One replay window can contain several finalized words from different cache slots.
    for window_row in continuity_windows:
        # Each timing/count span represents wording moved to one visible identity.
        for folded_span in window_row.get("folded_word_spans", []):
            start_seconds = float(folded_span["start_seconds"])
            end_seconds = float(folded_span["end_seconds"])
            visible_speaker_slot = str(folded_span["visible_speaker_slot"])
            expected_role = expected_role_for_fold(
                scorer_module,
                start_seconds,
                end_seconds,
                intervals,
            )
            visible_roles = visible_roles_for_fold(
                scorer_module,
                visible_segments,
                visible_speaker_slot,
                start_seconds,
                end_seconds,
            )
            scored_events.append(
                {
                    "window_index": int(window_row.get("window_index", 0) or 0),
                    "origin_speaker_slot": str(folded_span["origin_speaker_slot"]),
                    "visible_speaker_slot": visible_speaker_slot,
                    "start_seconds": start_seconds,
                    "end_seconds": end_seconds,
                    "word_count": int(folded_span.get("word_count", 0) or 0),
                    "expected_role": expected_role,
                    "visible_roles": visible_roles,
                    "classification": classify_fold(expected_role, visible_roles),
                }
            )

    return scored_events


def unique_fold_keys(
    fold_events: list[dict[str, Any]],
    classification: str | None = None,
) -> set[tuple[str, str, float, float]]:
    """Return distinct origin/target/timing folds after decoder revisions.

    Args:
        fold_events: Scored folds; empty means the replay produced no aliases.
        classification: Optional label filter; None keeps every classified fold.

    Returns:
        Unique PHI-safe keys; an empty set means no event matched the requested class.
    """
    # Repeated stabilization windows can report the same visible wording more than once.
    return {
        (
            str(event["origin_speaker_slot"]),
            str(event["visible_speaker_slot"]),
            float(event["start_seconds"]),
            float(event["end_seconds"]),
        )
        for event in fold_events
        if classification is None or event["classification"] == classification
    }


def build_report(
    continuity_path: Path,
    history_path: Path,
    textgrid_paths: list[Path],
    baseline_history_path: Path | None = None,
) -> dict[str, Any]:
    """Build one deterministic replay report for operator release review.

    Args:
        continuity_path: Instrumented JSONL; empty content means no windows were captured.
        history_path: Settled history object; empty segments mean no visible transcript.
        textgrid_paths: Doctor/Patient truth files; an empty list leaves ownership unresolved.
        baseline_history_path: Phase 0 UI history, or None to omit delta comparison.

    Returns:
        Session ID, aggregate counts, and PHI-safe events; empty folds produce zero counts.
    """
    scorer_module = load_transcript_quality_module()
    history = load_json_object(history_path)
    visible_segments = scorer_module.history_segments(history)
    intervals = reference_intervals(scorer_module, textgrid_paths)
    fold_events = score_fold_events(
        scorer_module,
        load_continuity_windows(continuity_path),
        visible_segments,
        intervals,
    )

    # Operators request Phase 0 only for the approved same-span release comparison.
    if baseline_history_path is not None:
        baseline_history = load_json_object(baseline_history_path)
        baseline_segments = scorer_module.history_segments(baseline_history)
        add_attribution_deltas(scorer_module, fold_events, baseline_segments)

    report = {
        "session_id": str(history.get("session_id", "")),
        "summary": {
            "fold_events": len(fold_events),
            "unique_fold_spans": len(unique_fold_keys(fold_events)),
            "unique_harmful_cross_speaker_folds": len(
                unique_fold_keys(fold_events, "harmful_cross_speaker_fold")
            ),
            "unique_benign_same_role_folds": len(
                unique_fold_keys(fold_events, "benign_same_role_fold")
            ),
            "unique_unresolved_folds": len(
                unique_fold_keys(fold_events, "unresolved_fold")
            ),
        },
        "fold_events": fold_events,
    }

    # Legacy diagnostic runs keep their prior JSON shape when no baseline was supplied.
    if baseline_history_path is not None:
        report["attribution_delta"] = summarize_attribution_deltas(fold_events)

    return report


def parse_args() -> argparse.Namespace:
    """Parse the retained replay artifacts and role-qualified TextGrid pair.

    Returns:
        CLI paths; missing positional values cause argparse to stop before scoring.
    """
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument("window_continuity", type=Path)
    argument_parser.add_argument("history", type=Path)
    argument_parser.add_argument("doctor_textgrid", type=Path)
    argument_parser.add_argument("patient_textgrid", type=Path)
    argument_parser.add_argument(
        "--baseline-history",
        type=Path,
        help="Phase 0 history used to compare user-visible attribution at candidate folds",
    )
    return argument_parser.parse_args()


def main() -> int:
    """Print the grounded fold report consumed by the fold acceptance evidence.

    Returns:
        Process exit code; zero means a complete JSON report was written to stdout.
    """
    arguments = parse_args()
    report = build_report(
        arguments.window_continuity,
        arguments.history,
        [arguments.doctor_textgrid, arguments.patient_textgrid],
        arguments.baseline_history,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


# A direct operator run reads retained evidence and writes one report without runtime changes.
if __name__ == "__main__":
    raise SystemExit(main())
