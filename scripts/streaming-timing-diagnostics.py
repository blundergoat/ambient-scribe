#!/usr/bin/env python3
"""Characterize streaming timing separately from speaker ownership.

The timing section scores emitted spans against TextGrid time. The ownership
section aligns text without reading emitted timestamps and is suitable for
before/after timing comparisons.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from collections import defaultdict
from pathlib import Path
from types import ModuleType
from typing import Any, Sequence

ROOT_DIR = Path(__file__).resolve().parents[1]
ALIGNMENT_MODULE_PATH = ROOT_DIR / "scripts" / "transcript_alignment.py"
DEFAULT_BOUNDARY_MS = (150, 250, 400)
SUPPORTED_ROLES = ("DOCTOR", "PATIENT")
FLOOR_SECONDS = 0.05
EPSILON = 1e-6


def load_alignment_module() -> ModuleType:
    """Load the sibling helper without mutating ``sys.path``."""
    specification = importlib.util.spec_from_file_location(
        "streaming_timing_transcript_alignment",
        ALIGNMENT_MODULE_PATH,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f"cannot load alignment helper: {ALIGNMENT_MODULE_PATH}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


ALIGNMENT = load_alignment_module()


def _sha256(path: Path) -> str:
    """Return the byte identity of one sealed input."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _row_words(row: dict[str, Any]) -> list[str]:
    """Return normalized display words for one history row."""
    return ALIGNMENT.normalize_words(str(row.get("text", "")))


def _is_floor_row(row: dict[str, Any]) -> bool:
    """Return whether a row carries the exact synthetic duration floor."""
    start = float(row.get("start", 0.0) or 0.0)
    end = float(row.get("end", 0.0) or 0.0)
    return abs((end - start) - FLOOR_SECONDS) < EPSILON


def _reference_intervals(
    doctor_path: Path,
    patient_path: Path,
    cutoff_seconds: float,
) -> list[Any]:
    """Return both role channels in deterministic time order."""
    intervals = [
        *ALIGNMENT.parse_textgrid_intervals(
            doctor_path,
            "DOCTOR",
            cutoff_seconds=cutoff_seconds,
        ),
        *ALIGNMENT.parse_textgrid_intervals(
            patient_path,
            "PATIENT",
            cutoff_seconds=cutoff_seconds,
        ),
    ]
    return sorted(
        intervals,
        key=lambda interval: (
            interval.start,
            interval.end,
            interval.role,
            interval.interval_ordinal,
        ),
    )


def _overlap_seconds(
    start: float,
    end: float,
    interval_start: float,
    interval_end: float,
) -> float:
    """Return positive-duration overlap between two spans."""
    return max(0.0, min(end, interval_end) - max(start, interval_start))


def _reference_role_for_span(
    start: float,
    end: float,
    intervals: Sequence[Any],
) -> str | None:
    """Return the unique role with greatest emitted-span overlap."""
    overlap_by_role = {role: 0.0 for role in SUPPORTED_ROLES}
    for interval in intervals:
        overlap_by_role[interval.role] += _overlap_seconds(
            start,
            end,
            interval.start,
            interval.end,
        )
    best_overlap = max(overlap_by_role.values())
    best_roles = [
        role for role, overlap in overlap_by_role.items() if overlap == best_overlap
    ]
    if best_overlap <= 0.0 or len(best_roles) != 1:
        return None
    return best_roles[0]


def _reference_role_at_point(
    point: float,
    intervals: Sequence[Any],
) -> str | None:
    """Return the sole TextGrid role active at one emitted start point."""
    active_roles = {
        interval.role
        for interval in intervals
        if interval.start - EPSILON <= point < interval.end - EPSILON
    }
    if len(active_roles) != 1:
        return None
    return next(iter(active_roles))


def _reference_overlap_spans(intervals: Sequence[Any]) -> list[tuple[float, float]]:
    """Return merged Doctor/Patient cross-talk spans."""
    doctor_intervals = [interval for interval in intervals if interval.role == "DOCTOR"]
    patient_intervals = [
        interval for interval in intervals if interval.role == "PATIENT"
    ]
    spans = []
    for doctor_interval in doctor_intervals:
        for patient_interval in patient_intervals:
            start = max(doctor_interval.start, patient_interval.start)
            end = min(doctor_interval.end, patient_interval.end)
            if end > start:
                spans.append((start, end))

    merged: list[tuple[float, float]] = []
    for start, end in sorted(spans):
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        previous_start, previous_end = merged[-1]
        merged[-1] = (previous_start, max(previous_end, end))
    return merged


def _row_touches_spans(
    start: float,
    end: float,
    spans: Sequence[tuple[float, float]],
) -> bool:
    """Return whether one emitted row intersects reference cross-talk."""
    return any(
        _overlap_seconds(start, end, span_start, span_end) > 0
        for span_start, span_end in spans
    )


def _turn_boundaries(intervals: Sequence[Any]) -> list[float]:
    """Return every non-empty TextGrid speech start and end boundary."""
    return sorted(
        {
            boundary
            for interval in intervals
            for boundary in (interval.start, interval.end)
        }
    )


def _new_cell() -> dict[str, int | float | None]:
    """Return an empty row/word placement cell."""
    return {
        "rows": 0,
        "correct_rows": 0,
        "display_words": 0,
        "correct_display_words": 0,
        "row_accuracy_percent": None,
        "word_accuracy_percent": None,
    }


def _add_to_cell(
    cell: dict[str, int | float | None],
    *,
    word_count: int,
    correct: bool,
) -> None:
    """Add one scoreable history row to a placement cell."""
    cell["rows"] = int(cell["rows"]) + 1
    cell["display_words"] = int(cell["display_words"]) + word_count
    if correct:
        cell["correct_rows"] = int(cell["correct_rows"]) + 1
        cell["correct_display_words"] = int(cell["correct_display_words"]) + word_count


def _finish_cell(
    cell: dict[str, int | float | None],
) -> dict[str, int | float | None]:
    """Populate percentages after a placement cell is counted."""
    rows = int(cell["rows"])
    words = int(cell["display_words"])
    cell["row_accuracy_percent"] = (
        round(int(cell["correct_rows"]) / rows * 100, 6) if rows else None
    )
    cell["word_accuracy_percent"] = (
        round(int(cell["correct_display_words"]) / words * 100, 6) if words else None
    )
    return cell


def _span_based_placement(
    rows: Sequence[dict[str, Any]],
    intervals: Sequence[Any],
) -> dict[str, Any]:
    """Mirror transcript-quality strict non-overlap attribution."""
    overlap_spans = _reference_overlap_spans(intervals)
    cell = _new_cell()
    unscored_no_reference = 0
    excluded_overlap = 0
    for row in rows:
        start = float(row.get("start", 0.0) or 0.0)
        end = float(row.get("end", 0.0) or 0.0)
        expected_role = _reference_role_for_span(start, end, intervals)
        if expected_role is None:
            unscored_no_reference += 1
            continue
        if _row_touches_spans(start, end, overlap_spans):
            excluded_overlap += 1
            continue
        _add_to_cell(
            cell,
            word_count=len(_row_words(row)),
            correct=str(row.get("role", "")).upper() == expected_role,
        )
    return {
        **_finish_cell(cell),
        "unscored_no_reference_rows": unscored_no_reference,
        "excluded_overlap_rows": excluded_overlap,
    }


def _start_point_cells(
    rows: Sequence[dict[str, Any]],
    intervals: Sequence[Any],
    boundary_milliseconds: Sequence[int],
) -> dict[str, Any]:
    """Score row starts by floor class and distance from a true turn boundary."""
    boundaries = _turn_boundaries(intervals)
    cells: dict[str, Any] = {}
    for boundary_ms in boundary_milliseconds:
        tolerance_seconds = boundary_ms / 1000
        tolerance_cells = {
            "floor": {
                "near_boundary": _new_cell(),
                "away_from_boundary": _new_cell(),
                "unscored_rows": 0,
                "near_boundary_examples": [],
            },
            "non_floor": {
                "near_boundary": _new_cell(),
                "away_from_boundary": _new_cell(),
                "unscored_rows": 0,
                "near_boundary_examples": [],
            },
        }
        for row_index, row in enumerate(rows):
            start = float(row.get("start", 0.0) or 0.0)
            end = float(row.get("end", 0.0) or 0.0)
            expected_role = _reference_role_at_point(start, intervals)
            span_class = "floor" if _is_floor_row(row) else "non_floor"
            if expected_role is None:
                tolerance_cells[span_class]["unscored_rows"] += 1
                continue
            nearest_boundary = min(
                (abs(start - boundary) for boundary in boundaries),
                default=float("inf"),
            )
            region = (
                "near_boundary"
                if nearest_boundary <= tolerance_seconds + EPSILON
                else "away_from_boundary"
            )
            visible_role = str(row.get("role", "")).upper()
            _add_to_cell(
                tolerance_cells[span_class][region],
                word_count=len(_row_words(row)),
                correct=visible_role == expected_role,
            )
            examples = tolerance_cells[span_class]["near_boundary_examples"]
            if region == "near_boundary" and len(examples) < 10:
                examples.append(
                    {
                        "row_index": row_index,
                        "speaker_id": str(row.get("speaker_id", "")),
                        "visible_role": visible_role,
                        "expected_role_at_start": expected_role,
                        "correct": visible_role == expected_role,
                        "start": start,
                        "end": end,
                        "nearest_boundary": round(
                            min(boundaries, key=lambda boundary: abs(start - boundary)),
                            9,
                        ),
                        "distance_to_boundary_ms": round(
                            nearest_boundary * 1000,
                            6,
                        ),
                        "display_words": len(_row_words(row)),
                    }
                )

        for span_class in ("floor", "non_floor"):
            for region in ("near_boundary", "away_from_boundary"):
                _finish_cell(tolerance_cells[span_class][region])
        cells[str(boundary_ms)] = tolerance_cells
    return {
        "boundary_definition": (
            "every start and end of a non-empty Doctor or Patient TextGrid "
            "speech interval"
        ),
        "boundary_count": len(boundaries),
        "tolerances_ms": cells,
    }


def _start_point_span_totals(
    rows: Sequence[dict[str, Any]],
    intervals: Sequence[Any],
) -> dict[str, Any]:
    """Score every point-alignable row by floor class without boundary bins."""
    cells = {
        "floor": {**_new_cell(), "unscored_rows": 0},
        "non_floor": {**_new_cell(), "unscored_rows": 0},
    }
    for row in rows:
        span_class = "floor" if _is_floor_row(row) else "non_floor"
        start = float(row.get("start", 0.0) or 0.0)
        expected_role = _reference_role_at_point(start, intervals)
        if expected_role is None:
            cells[span_class]["unscored_rows"] += 1
            continue
        _add_to_cell(
            cells[span_class],
            word_count=len(_row_words(row)),
            correct=str(row.get("role", "")).upper() == expected_role,
        )
    for cell in cells.values():
        _finish_cell(cell)
    return cells


def _ownership_cells(matches: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Score speaker slots and visible roles on exact forced text matches."""
    confusion: dict[str, dict[str, int]] = {}
    role_scored = {role: 0 for role in SUPPORTED_ROLES}
    visible_correct = 0
    visible_supported = 0
    for match in matches:
        reference = match["reference"]
        hypothesis = match["hypothesis"]
        reference_role = str(reference["role"])
        speaker_id = str(hypothesis["speaker_id"])
        visible_role = str(hypothesis["visible_role"])
        role_scored[reference_role] += 1
        speaker_counts = confusion.setdefault(
            speaker_id,
            {role: 0 for role in SUPPORTED_ROLES},
        )
        speaker_counts[reference_role] += 1
        if visible_role in SUPPORTED_ROLES:
            visible_supported += 1
            visible_correct += int(visible_role == reference_role)

    speaker_ids = sorted(confusion)
    free_mapping: dict[str, str] = {}
    free_correct = 0
    for speaker_id in speaker_ids:
        best_role = max(
            SUPPORTED_ROLES,
            key=lambda role: (confusion[speaker_id][role], role),
        )
        free_mapping[speaker_id] = best_role
        free_correct += confusion[speaker_id][best_role]

    dyadic_mapping: dict[str, str] = {}
    dyadic_correct = 0
    if len(speaker_ids) == 2:
        first_speaker, second_speaker = speaker_ids
        candidates = (
            {first_speaker: "DOCTOR", second_speaker: "PATIENT"},
            {first_speaker: "PATIENT", second_speaker: "DOCTOR"},
        )
        ranked = []
        for candidate in candidates:
            correct = sum(
                confusion[speaker_id][role] for speaker_id, role in candidate.items()
            )
            ranked.append((correct, json.dumps(candidate, sort_keys=True), candidate))
        dyadic_correct, _, dyadic_mapping = max(ranked)

    scored_words = len(matches)
    return {
        "scored_words": scored_words,
        "reference_role_counts": role_scored,
        "reference_role_confusion_by_speaker": dict(sorted(confusion.items())),
        "unconstrained_oracle": {
            "mapping": free_mapping,
            "correct_words": free_correct,
            "accuracy_percent": (
                round(free_correct / scored_words * 100, 6) if scored_words else None
            ),
        },
        "valid_dyadic_slot_purity": {
            "available": bool(dyadic_mapping),
            "mapping": dyadic_mapping,
            "correct_words": dyadic_correct,
            "accuracy_percent": (
                round(dyadic_correct / scored_words * 100, 6)
                if scored_words and dyadic_mapping
                else None
            ),
        },
        "visible_role_correctness": {
            "supported_role_words": visible_supported,
            "correct_words": visible_correct,
            "unsupported_role_words": scored_words - visible_supported,
            "accuracy_percent": (
                round(visible_correct / scored_words * 100, 6) if scored_words else None
            ),
        },
    }


def _ownership_section(
    rows: Sequence[dict[str, Any]],
    reference_corpus: Any,
) -> tuple[dict[str, Any], list[Any], dict[str, Any]]:
    """Build the timestamp-independent ownership section and internals."""
    hypothesis_words = ALIGNMENT.flatten_hypothesis_rows(rows)
    alignment = ALIGNMENT.align_role_channels(
        reference_corpus.words_by_role,
        hypothesis_words,
    )
    linearized_content_alignment = ALIGNMENT.word_error_alignment(
        [word.token for word in reference_corpus.linearized_words],
        [word.token for word in hypothesis_words],
    )
    ownership_cells = _ownership_cells(alignment["matches"])
    hypothesis_word_count = len(hypothesis_words)
    section = {
        "metric_contract": (
            "each role channel is aligned independently; cross-role conflicts "
            "remain unscored and emitted start/end fields are not read"
        ),
        "reference_words": alignment["reference_words"],
        "reference_overlap_order_words": len(reference_corpus.overlap_ambiguous_words),
        "hypothesis_words": hypothesis_word_count,
        "unambiguous_alignment_coverage_percent": (
            round(
                alignment["unambiguous_match_count"] / hypothesis_word_count * 100,
                6,
            )
            if hypothesis_word_count
            else None
        ),
        "alignment": alignment,
        "linearized_content_edit_diagnostic": {
            "contract": (
                "S/I/D accounting only; the arbitrary cross-channel "
                "linearization is never used for ownership"
            ),
            **linearized_content_alignment,
        },
        **ownership_cells,
    }
    return section, hypothesis_words, ownership_cells


def _span_class_ownership_context(
    matches: Sequence[dict[str, Any]],
    hypothesis_words: Sequence[Any],
) -> dict[str, Any]:
    """Split aligned visible-role cells by the timestamp-derived floor class."""
    cells = {"floor": _new_cell(), "non_floor": _new_cell()}
    for match in matches:
        hypothesis_index = int(match["hypothesis_index"])
        hypothesis_word = hypothesis_words[hypothesis_index]
        reference_role = str(match["reference"]["role"])
        span_class = "floor" if hypothesis_word.floor_span else "non_floor"
        _add_to_cell(
            cells[span_class],
            word_count=1,
            correct=hypothesis_word.role == reference_role,
        )
    return {span_class: _finish_cell(cell) for span_class, cell in cells.items()}


def _word_stream_fingerprint(rows: Sequence[dict[str, Any]]) -> str:
    """Hash ordered wording and ownership without row partition or timing."""
    word_stream = [
        (
            token,
            str(row.get("speaker_id", "")),
            str(row.get("role", "")).upper(),
        )
        for row in rows
        for token in _row_words(row)
    ]
    return hashlib.sha256(
        json.dumps(word_stream, separators=(",", ":")).encode()
    ).hexdigest()


def _structural_nulls(
    rows: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Measure row-count projections without moving or rescoring any word."""
    grouped_rows: dict[tuple[str, float], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped_rows[
            (
                str(row.get("speaker_id", "")),
                float(row.get("start", 0.0) or 0.0),
            )
        ].append(row)
    multi_groups = [group for group in grouped_rows.values() if len(group) > 1]
    floor_rows_in_groups = sum(
        _is_floor_row(row) for group in multi_groups for row in group
    )
    real_rows_in_groups = sum(
        not _is_floor_row(row) for group in multi_groups for row in group
    )
    all_group_removal = sum(len(group) - 1 for group in multi_groups)
    floor_only_removal = sum(
        max(0, sum(_is_floor_row(row) for row in group) - 1) for group in multi_groups
    )
    return {
        "contract": (
            "row-count projection only; words retain original display order, "
            "speaker_id, and role"
        ),
        "same_slot_start_multi_groups": len(multi_groups),
        "rows_in_groups": sum(len(group) for group in multi_groups),
        "floor_rows_in_groups": floor_rows_in_groups,
        "real_span_rows_in_groups": real_rows_in_groups,
        "word_stream_sha256": _word_stream_fingerprint(rows),
        "all_group": {
            "rows_removed": all_group_removal,
            "word_stream_invariant": True,
            "ownership_cells_invariant": True,
        },
        "floor_only": {
            "rows_removed": floor_only_removal,
            "word_stream_invariant": True,
            "ownership_cells_invariant": True,
        },
    }


def evaluate_history(
    history: dict[str, Any],
    *,
    run_id: str,
    doctor_path: Path,
    patient_path: Path,
    cutoff_seconds: float,
    boundary_milliseconds: Sequence[int],
) -> dict[str, Any]:
    """Evaluate one saved history without modifying it."""
    raw_rows = history.get("segments", [])
    if not isinstance(raw_rows, list):
        raise ValueError("history segments must be a list")
    rows = [dict(row) for row in raw_rows]
    intervals = _reference_intervals(
        doctor_path,
        patient_path,
        cutoff_seconds,
    )
    reference_corpus = ALIGNMENT.build_reference_corpus(
        doctor_path,
        patient_path,
        cutoff_seconds=cutoff_seconds,
    )
    ownership, hypothesis_words, _ = _ownership_section(
        rows,
        reference_corpus,
    )
    timing_placement = {
        "span_based_strict_non_overlap": _span_based_placement(rows, intervals),
        "start_point_by_boundary_distance": _start_point_cells(
            rows,
            intervals,
            boundary_milliseconds,
        ),
        "text_aligned_visible_role_by_span_class": (
            _span_class_ownership_context(
                ownership["alignment"]["matches"],
                hypothesis_words,
            )
        ),
    }
    start_point_span_totals = _start_point_span_totals(rows, intervals)
    timing_placement["healthy_class_upper_bound"] = {
        "contract": (
            "if floor rows reached the non-floor start-point rate, the "
            "projected timing-placement rate would equal this value; it is "
            "not a speaker-slot or visible-role forecast"
        ),
        "non_floor_start_point": start_point_span_totals["non_floor"],
        "projected_timing_placement_accuracy_percent": (
            start_point_span_totals["non_floor"]["row_accuracy_percent"]
        ),
    }

    floor_rows = sum(_is_floor_row(row) for row in rows)
    floor_words = sum(len(_row_words(row)) for row in rows if _is_floor_row(row))
    return {
        "run_id": run_id,
        "cutoff_seconds": cutoff_seconds,
        "row_accounting": {
            "total": len(rows),
            "floor": floor_rows,
            "non_floor": len(rows) - floor_rows,
        },
        "display_word_accounting": {
            "total": len(hypothesis_words),
            "floor": floor_words,
            "non_floor": len(hypothesis_words) - floor_words,
        },
        "timing_placement": timing_placement,
        "text_aligned_speaker_ownership": ownership,
        "structural_nulls": _structural_nulls(rows),
    }


def build_verdict(findings: dict[str, Any]) -> str:
    """Return the human-readable separation of timing and ownership results."""
    lines = [
        "# Streaming timing characterization",
        "",
        "Timing placement, speaker-slot ownership, and visible roles are "
        "reported separately.",
        "",
    ]
    for run in findings["runs"]:
        span_score = run["timing_placement"]["span_based_strict_non_overlap"]
        ownership = run["text_aligned_speaker_ownership"]
        nulls = run["structural_nulls"]
        lines.extend(
            [
                f"## {run['run_id']}",
                "",
                "- Timestamp-derived strict non-overlap placement: "
                f"{span_score['row_accuracy_percent']}% "
                f"({span_score['correct_rows']}/{span_score['rows']} rows).",
                "- Timestamp-independent exact-alignment coverage: "
                f"{ownership['unambiguous_alignment_coverage_percent']}% "
                f"({ownership['scored_words']}/{ownership['hypothesis_words']} "
                "display words).",
                "- Timing-placement upper bound if floor rows reached the "
                "non-floor start-point rate: "
                f"{run['timing_placement']['healthy_class_upper_bound']['projected_timing_placement_accuracy_percent']}%; "
                "not a speaker-label forecast.",
                "- Timestamp-independent visible-role correctness: "
                f"{ownership['visible_role_correctness']['accuracy_percent']}%.",
                "- Timestamp-independent best valid dyadic slot purity: "
                f"{ownership['valid_dyadic_slot_purity']['accuracy_percent']}%.",
                "- Structural nulls remove "
                f"{nulls['all_group']['rows_removed']} all-group rows versus "
                f"{nulls['floor_only']['rows_removed']} floor-only rows; "
                f"{nulls['real_span_rows_in_groups']} real-span rows explain "
                "the difference.",
                "",
            ]
        )
    lines.extend(
        [
            "## Interpretation",
            "",
            "The non-floor start-point rate is a timing-placement comparison "
            "class only. It is not a forecast of speaker-slot purity or visible "
            "role correctness. Captured histories do not contain the true "
            "per-word NeMo timing needed to simulate M02.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    """Parse one sealed characterization run."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="append", type=Path, required=True)
    parser.add_argument("--doctor", type=Path, required=True)
    parser.add_argument("--patient", type=Path, required=True)
    parser.add_argument("--cutoff-seconds", type=float, default=557.0)
    parser.add_argument(
        "--boundary-ms",
        action="append",
        type=int,
        default=None,
        help="Repeat for multiple tolerances; defaults to 150, 250, and 400.",
    )
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--verdict", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    """Evaluate selected histories and write deterministic evidence."""
    args = parse_args()
    boundary_milliseconds = tuple(args.boundary_ms or DEFAULT_BOUNDARY_MS)
    runs = []
    for history_path in args.history:
        history = json.loads(history_path.read_text(encoding="utf-8"))
        run = evaluate_history(
            history,
            run_id=history_path.parent.name,
            doctor_path=args.doctor,
            patient_path=args.patient,
            cutoff_seconds=args.cutoff_seconds,
            boundary_milliseconds=boundary_milliseconds,
        )
        run["history_sha256"] = _sha256(history_path)
        runs.append(run)

    findings = {
        "schema_version": 1,
        "metric_contract": {
            "timing_placement": "uses emitted start/end fields",
            "text_aligned_speaker_ownership": (
                "does not read emitted start/end fields"
            ),
        },
        "reference_inputs": {
            "doctor_sha256": _sha256(args.doctor),
            "patient_sha256": _sha256(args.patient),
        },
        "runs": runs,
    }
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(
        json.dumps(findings, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if args.verdict is not None:
        args.verdict.parent.mkdir(parents=True, exist_ok=True)
        args.verdict.write_text(build_verdict(findings), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
