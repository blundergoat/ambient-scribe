#!/usr/bin/env python3
"""Classify corrected-transcript insertions from PHI-safe allocation evidence.

The primary S/I/D alignment reads transcript wording in display order but never
uses emitted row timestamps. Output contains indices, counts, source classes,
and hashes only; it never repeats transcript or TextGrid wording.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Sequence

ROOT_DIR = Path(__file__).resolve().parents[1]
ALIGNMENT_MODULE_PATH = ROOT_DIR / "scripts" / "transcript_alignment.py"
DISPLAY_WORD_PATTERN = re.compile(r"\S+")
CLASS_NAMES = (
    "retained_live_fallback",
    "chunk_seam_duplicate",
    "duplicate_allocation_source",
    "other_corrected_asr",
    "ambiguous_unclassified",
)
CLASS_PRIORITY = (
    "retained_live_fallback",
    "duplicate_allocation_source",
    "chunk_seam_duplicate",
    "ambiguous_unclassified",
    "other_corrected_asr",
)
MIN_SEAM_PHRASE_WORDS = 2
MAX_SEAM_PHRASE_WORDS = 8


def load_alignment_module() -> ModuleType:
    """Load the sealed sibling alignment helper without changing ``sys.path``."""
    specification = importlib.util.spec_from_file_location(
        "corrected_insertion_transcript_alignment",
        ALIGNMENT_MODULE_PATH,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("cannot load transcript alignment helper")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


ALIGNMENT = load_alignment_module()


def _sha256(path: Path) -> str:
    """Return the byte identity of one classifier input."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    """Load one JSON input without echoing its content on failure."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read valid JSON from {path.name}") from error


def _segments(payload: Any, lane: str) -> list[dict[str, Any]]:
    """Return ordered transcript rows from a history or corrected artifact."""
    raw_rows = payload.get("segments") if isinstance(payload, dict) else payload
    if not isinstance(raw_rows, list) or any(
        not isinstance(row, dict) for row in raw_rows
    ):
        raise ValueError(f"{lane} transcript must contain a segments list")
    return [dict(row) for row in raw_rows]


def _integer(value: Any, context: str, *, minimum: int = 0) -> int:
    """Return a JSON integer after rejecting booleans and negative values."""
    if type(value) is not int or value < minimum:
        raise ValueError(f"{context} must be an integer >= {minimum}")
    return value


def _exact_keys(
    value: Any,
    required: set[str],
    context: str,
    *,
    optional: set[str] | None = None,
) -> dict[str, Any]:
    """Require a closed object schema so wording cannot enter diagnostics."""
    optional = optional or set()
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be an object")
    keys = set(value)
    if not required.issubset(keys) or not keys.issubset(required | optional):
        raise ValueError(f"{context} has an unsupported schema")
    return value


def _allocation_payload(artifact: Any) -> dict[str, Any]:
    """Return the allocation object from a runner artifact or direct fixture."""
    if not isinstance(artifact, dict):
        raise ValueError("allocation artifact must be an object")
    allocation = artifact.get("allocation", artifact)
    return _exact_keys(
        allocation,
        {
            "schema_version",
            "allocation_mode",
            "corrected_asr_words",
            "allocated_asr_words",
            "retained_live_words",
            "final_display_words",
            "rows",
            "accounting",
            "chunk_provenance",
        },
        "allocation",
    )


def validate_allocation_artifact(artifact: Any) -> dict[str, Any]:
    """Validate allocation accounting and return an output-index source map."""
    allocation = _allocation_payload(artifact)
    if allocation["schema_version"] != 1:
        raise ValueError("unsupported allocation schema version")
    if allocation["allocation_mode"] not in {
        "empty",
        "anchored",
        "global_proportional",
        "unscaffolded",
    }:
        raise ValueError("unsupported allocation mode")

    corrected_asr_words = _integer(
        allocation["corrected_asr_words"],
        "corrected ASR word count",
    )
    final_display_words = _integer(
        allocation["final_display_words"],
        "final display word count",
    )
    allocated_asr_words = _integer(
        allocation["allocated_asr_words"],
        "allocated ASR word count",
    )
    retained_live_words = _integer(
        allocation["retained_live_words"],
        "retained live word count",
    )

    rows = allocation["rows"]
    if not isinstance(rows, list):
        raise ValueError("allocation rows must be a list")
    output_sources: list[dict[str, Any] | None] = [None] * final_display_words
    corrected_source_counts = [0] * corrected_asr_words
    output_cursor = 0
    observed_asr_words = 0
    observed_retained_words = 0
    for expected_row_index, raw_row in enumerate(rows):
        row = _exact_keys(
            raw_row,
            {
                "row_index",
                "segment_id",
                "output_start_index",
                "output_end_index",
                "output_word_count",
                "anchor_outcome",
                "anchor_score_class",
                "anchor_score",
                "anchor_clamped",
                "source_runs",
            },
            "allocation row",
        )
        if row["row_index"] != expected_row_index:
            raise ValueError("allocation row indices are not contiguous")
        if re.fullmatch(r"corrected-\d{4}", str(row["segment_id"])) is None:
            raise ValueError("allocation segment ID is invalid")
        row_start = _integer(row["output_start_index"], "row output start")
        row_end = _integer(row["output_end_index"], "row output end")
        row_word_count = _integer(
            row["output_word_count"],
            "row output word count",
        )
        if (
            row_start != output_cursor
            or row_end != row_start + row_word_count
            or row_end > final_display_words
        ):
            raise ValueError("allocation row output accounting is inconsistent")
        if row["anchor_outcome"] not in {"matched", "missing", "not_observed"}:
            raise ValueError("allocation row has an invalid anchor outcome")
        if row["anchor_score_class"] not in {
            "high",
            "accepted",
            "missing",
            "not_observed",
        }:
            raise ValueError("allocation row has an invalid anchor score class")
        score = row["anchor_score"]
        if score is not None and (
            isinstance(score, bool)
            or not isinstance(score, int | float)
            or not 0.0 <= score <= 1.0
        ):
            raise ValueError("allocation row has an invalid anchor score")
        if type(row["anchor_clamped"]) is not bool:
            raise ValueError("allocation row has an invalid clamped flag")

        source_runs = row["source_runs"]
        if not isinstance(source_runs, list):
            raise ValueError("allocation source runs must be a list")
        run_cursor = row_start
        for expected_run_index, raw_source_run in enumerate(source_runs):
            source_run = _exact_keys(
                raw_source_run,
                {
                    "source",
                    "source_start_index",
                    "source_end_index",
                    "word_count",
                    "source_run_index",
                    "output_start_index",
                    "output_end_index",
                },
                "allocation source run",
                optional={"source_row_index"},
            )
            source = source_run["source"]
            if source not in {"corrected_asr", "retained_live"}:
                raise ValueError("allocation source is invalid")
            if source_run["source_run_index"] != expected_run_index:
                raise ValueError("source run indices are not contiguous")
            source_start = _integer(
                source_run["source_start_index"],
                "source run start",
            )
            source_end = _integer(source_run["source_end_index"], "source run end")
            word_count = _integer(source_run["word_count"], "source run word count")
            output_start = _integer(
                source_run["output_start_index"],
                "source run output start",
            )
            output_end = _integer(
                source_run["output_end_index"],
                "source run output end",
            )
            if (
                source_end != source_start + word_count
                or output_start != run_cursor
                or output_end != output_start + word_count
                or output_end > row_end
            ):
                raise ValueError("allocation source run accounting is inconsistent")
            if "source_row_index" in source_run:
                _integer(source_run["source_row_index"], "source row index")

            for offset, output_index in enumerate(
                range(output_start, output_end),
            ):
                source_index = source_start + offset
                if output_sources[output_index] is not None:
                    raise ValueError("one display word has multiple allocation sources")
                if source == "corrected_asr":
                    if source_index >= corrected_asr_words:
                        raise ValueError("corrected ASR source index is out of range")
                    corrected_source_counts[source_index] += 1
                    observed_asr_words += 1
                else:
                    observed_retained_words += 1
                output_sources[output_index] = {
                    "source": source,
                    "source_index": source_index,
                    "row_index": expected_row_index,
                    "source_run_index": expected_run_index,
                }
            run_cursor = output_end
        if run_cursor != row_end:
            raise ValueError("allocation source runs do not cover their row")
        output_cursor = row_end

    if output_cursor != final_display_words or any(
        source is None for source in output_sources
    ):
        raise ValueError("allocation does not cover every display word")
    if observed_asr_words != allocated_asr_words:
        raise ValueError("allocated ASR total is inconsistent")
    if observed_retained_words != retained_live_words:
        raise ValueError("retained live total is inconsistent")

    duplicate_indices = [
        index for index, count in enumerate(corrected_source_counts) if count > 1
    ]
    unallocated_indices = [
        index for index, count in enumerate(corrected_source_counts) if count == 0
    ]
    accounting = _exact_keys(
        allocation["accounting"],
        {
            "source_run_words",
            "allocation_output_words",
            "output_word_coverage_complete",
            "final_output_matches_allocation",
            "duplicate_corrected_asr_source_indices",
            "unallocated_corrected_asr_source_indices",
        },
        "allocation accounting",
    )
    if (
        _integer(accounting["source_run_words"], "source run total")
        != final_display_words
        or _integer(
            accounting["allocation_output_words"],
            "allocation output total",
        )
        != final_display_words
        or accounting["output_word_coverage_complete"] is not True
        or accounting["final_output_matches_allocation"] is not True
    ):
        raise ValueError("allocation output accounting did not close")
    if accounting["duplicate_corrected_asr_source_indices"] != duplicate_indices:
        raise ValueError("duplicate corrected ASR accounting is inconsistent")
    if accounting["unallocated_corrected_asr_source_indices"] != unallocated_indices:
        raise ValueError("unallocated corrected ASR accounting is inconsistent")

    chunk_provenance = _exact_keys(
        allocation["chunk_provenance"],
        {"status", "ranges", "seam_indices"},
        "chunk provenance",
    )
    status = chunk_provenance["status"]
    ranges = chunk_provenance["ranges"]
    seam_indices = chunk_provenance["seam_indices"]
    if status not in {"observed", "not_observed"}:
        raise ValueError("chunk provenance status is invalid")
    if not isinstance(ranges, list) or not isinstance(seam_indices, list):
        raise ValueError("chunk provenance ranges must be lists")
    if status == "not_observed":
        if ranges or seam_indices:
            raise ValueError("unobserved chunk provenance must be empty")
    else:
        expected_start = 0
        observed_ranges = []
        for expected_chunk_index, raw_range in enumerate(ranges, start=1):
            chunk_range = _exact_keys(
                raw_range,
                {"chunk_index", "start_index", "end_index"},
                "chunk range",
            )
            chunk_index = _integer(
                chunk_range["chunk_index"],
                "chunk index",
                minimum=1,
            )
            start_index = _integer(chunk_range["start_index"], "chunk start")
            end_index = _integer(
                chunk_range["end_index"],
                "chunk end",
                minimum=1,
            )
            if (
                chunk_index != expected_chunk_index
                or start_index != expected_start
                or end_index <= start_index
                or end_index > corrected_asr_words
            ):
                raise ValueError("chunk ranges are not exact and contiguous")
            observed_ranges.append(
                {
                    "chunk_index": chunk_index,
                    "start_index": start_index,
                    "end_index": end_index,
                }
            )
            expected_start = end_index
        if expected_start != corrected_asr_words:
            raise ValueError("chunk ranges do not cover corrected ASR output")
        expected_seams = [item["end_index"] for item in observed_ranges[:-1]]
        if seam_indices != expected_seams:
            raise ValueError("chunk seam indices disagree with chunk ranges")

    return {
        "allocation": allocation,
        "output_sources": output_sources,
        "duplicate_source_indices": set(duplicate_indices),
        "corrected_asr_words": corrected_asr_words,
        "chunk_status": status,
        "seam_indices": list(seam_indices),
    }


def _display_word_mapping(
    rows: Sequence[dict[str, Any]],
) -> tuple[list[str], list[dict[str, Any]]]:
    """Map normalized hypothesis indices to whitespace display-word indices."""
    display_words: list[str] = []
    normalized_mapping: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows):
        normalized_row_word_index = 0
        for display_row_word_index, display_word in enumerate(
            DISPLAY_WORD_PATTERN.findall(str(row.get("text", "")).strip())
        ):
            output_word_index = len(display_words)
            display_words.append(display_word)
            pieces = ALIGNMENT.normalize_words(display_word)
            for normalized_piece_index, token in enumerate(pieces):
                normalized_mapping.append(
                    {
                        "token": token,
                        "output_word_index": output_word_index,
                        "row_index": row_index,
                        "normalized_row_word_index": normalized_row_word_index,
                        "display_row_word_index": display_row_word_index,
                        "normalized_piece_index": normalized_piece_index,
                    }
                )
                normalized_row_word_index += 1
    return display_words, normalized_mapping


def _source_word_representations(
    display_words: Sequence[str],
    output_sources: Sequence[dict[str, Any] | None],
    corrected_asr_words: int,
) -> list[tuple[str, ...] | None]:
    """Reconstruct normalized ASR source words only through exact allocation runs."""
    representations: list[tuple[str, ...] | None] = [None] * corrected_asr_words
    for output_index, display_word in enumerate(display_words):
        source = output_sources[output_index]
        if source is None or source["source"] != "corrected_asr":
            continue
        source_index = int(source["source_index"])
        representation = tuple(ALIGNMENT.normalize_words(display_word))
        existing = representations[source_index]
        if existing is not None and existing != representation:
            raise ValueError("duplicated ASR source index has conflicting wording")
        representations[source_index] = representation
    return representations


def _proven_chunk_seam_sources(
    representations: Sequence[tuple[str, ...] | None],
    seam_indices: Sequence[int],
) -> tuple[set[int], list[dict[str, int]]]:
    """Return source indices in exact 2-8-word repeats touching observed seams."""
    duplicate_sources: set[int] = set()
    matches: list[dict[str, int]] = []
    for seam_index in seam_indices:
        maximum_length = min(
            MAX_SEAM_PHRASE_WORDS,
            seam_index,
            len(representations) - seam_index,
        )
        for phrase_length in range(maximum_length, MIN_SEAM_PHRASE_WORDS - 1, -1):
            before = representations[seam_index - phrase_length : seam_index]
            after = representations[seam_index : seam_index + phrase_length]
            if any(item in {None, ()} for item in (*before, *after)) or before != after:
                continue
            # Standard WER may select either identical copy as the insertion,
            # so both sides remain attributable to the same proven seam repeat.
            duplicate_sources.update(
                range(seam_index - phrase_length, seam_index + phrase_length)
            )
            matches.append(
                {
                    "seam_index": seam_index,
                    "phrase_word_count": phrase_length,
                    "source_start_index": seam_index - phrase_length,
                    "source_end_index": seam_index + phrase_length,
                }
            )
            break
    return duplicate_sources, matches


def _overlap_ambiguous_hypothesis_indices(
    reference_corpus: Any,
    hypothesis_words: Sequence[Any],
) -> set[int]:
    """Return hypothesis indices whose exact identity depends on overlap order."""
    if not reference_corpus.overlap_ambiguous_words:
        return set()
    alignment = ALIGNMENT.align_transcript_words(
        reference_corpus.linearized_words,
        hypothesis_words,
    )
    overlap_identities = {
        word.identity for word in reference_corpus.overlap_ambiguous_words
    }
    overlap_tokens = {word.token for word in reference_corpus.overlap_ambiguous_words}
    ambiguous = {
        int(match["hypothesis_index"])
        for match in alignment["matches"]
        if tuple(match["reference"]["reference_id"]) in overlap_identities
    }
    ambiguous.update(
        hypothesis_index
        for hypothesis_index in alignment["ambiguous_hypothesis_indices"]
        if hypothesis_words[hypothesis_index].token in overlap_tokens
    )
    return ambiguous


def _metric_summary(alignment: dict[str, Any]) -> dict[str, int]:
    """Return S/I/D totals without the alignment's transcript tokens."""
    return {
        "reference_words": int(alignment["reference_words"]),
        "hypothesis_words": int(alignment["hypothesis_words"]),
        "substitutions": int(alignment["substitutions"]),
        "insertions": int(alignment["insertions"]),
        "deletions": int(alignment["deletions"]),
        "total_errors": int(alignment["total_errors"]),
    }


def evaluate_artifacts(
    live_payload: Any,
    corrected_payload: Any,
    allocation_artifact: Any,
    *,
    doctor_path: Path,
    patient_path: Path,
    cutoff_seconds: float,
) -> dict[str, Any]:
    """Return timestamp-independent S/I/D and corrected insertion classes."""
    if cutoff_seconds <= 0.0:
        raise ValueError("cutoff seconds must be positive")
    live_rows = _segments(live_payload, "live")
    corrected_rows = _segments(corrected_payload, "corrected")
    reference_corpus = ALIGNMENT.build_reference_corpus(
        doctor_path,
        patient_path,
        cutoff_seconds=cutoff_seconds,
    )
    reference_tokens = [word.token for word in reference_corpus.linearized_words]
    live_hypothesis_words = ALIGNMENT.flatten_hypothesis_rows(live_rows)
    corrected_hypothesis_words = ALIGNMENT.flatten_hypothesis_rows(corrected_rows)
    live_alignment = ALIGNMENT.word_error_alignment(
        reference_tokens,
        [word.token for word in live_hypothesis_words],
    )
    corrected_alignment = ALIGNMENT.word_error_alignment(
        reference_tokens,
        [word.token for word in corrected_hypothesis_words],
    )

    allocation = validate_allocation_artifact(allocation_artifact)
    display_words, normalized_mapping = _display_word_mapping(corrected_rows)
    if len(display_words) != allocation["allocation"]["final_display_words"]:
        raise ValueError("corrected transcript display words disagree with allocation")
    if [item["token"] for item in normalized_mapping] != [
        word.token for word in corrected_hypothesis_words
    ]:
        raise ValueError("corrected normalization mapping is inconsistent")

    representations = _source_word_representations(
        display_words,
        allocation["output_sources"],
        allocation["corrected_asr_words"],
    )
    seam_duplicate_sources, seam_matches = _proven_chunk_seam_sources(
        representations,
        allocation["seam_indices"] if allocation["chunk_status"] == "observed" else [],
    )
    overlap_ambiguous_indices = _overlap_ambiguous_hypothesis_indices(
        reference_corpus,
        corrected_hypothesis_words,
    )

    insertion_indices = [
        int(operation["hypothesis_index"])
        for operation in corrected_alignment["operations"]
        if operation["operation"] == "insertion"
    ]
    insertions = []
    class_counts = {class_name: 0 for class_name in CLASS_NAMES}
    for hypothesis_index in insertion_indices:
        normalized_word = normalized_mapping[hypothesis_index]
        output_word_index = int(normalized_word["output_word_index"])
        source = allocation["output_sources"][output_word_index]
        if source is None:
            raise ValueError("corrected insertion has no allocation source")
        source_name = str(source["source"])
        source_index = int(source["source_index"])

        if source_name == "retained_live":
            classification = "retained_live_fallback"
        elif source_index in allocation["duplicate_source_indices"]:
            classification = "duplicate_allocation_source"
        elif source_index in seam_duplicate_sources:
            classification = "chunk_seam_duplicate"
        elif hypothesis_index in overlap_ambiguous_indices:
            classification = "ambiguous_unclassified"
        else:
            classification = "other_corrected_asr"
        class_counts[classification] += 1

        insertion = {
            "hypothesis_index": hypothesis_index,
            "hypothesis_id": [
                int(normalized_word["row_index"]),
                int(normalized_word["normalized_row_word_index"]),
            ],
            "output_word_index": output_word_index,
            "display_row_word_index": int(normalized_word["display_row_word_index"]),
            "normalized_piece_index": int(normalized_word["normalized_piece_index"]),
            "source": source_name,
            "source_index": source_index,
            "allocation_row_index": int(source["row_index"]),
            "source_run_index": int(source["source_run_index"]),
            "classification": classification,
        }
        if classification == "ambiguous_unclassified":
            insertion["ambiguity_reason"] = "reference_overlap_order"
        insertions.append(insertion)

    if len(insertions) != corrected_alignment["insertions"]:
        raise RuntimeError("corrected insertion accounting did not close")

    primary = {
        "metric_contract": (
            "standard S/I/D over TextGrid reference order and transcript display "
            "order; emitted transcript start/end values are not read"
        ),
        "live": _metric_summary(live_alignment),
        "corrected": _metric_summary(corrected_alignment),
        "corrected_insertions": insertions,
        "class_counts": class_counts,
        "ambiguous_count": class_counts["ambiguous_unclassified"],
        "classification_coverage": {
            "expected_insertions": int(corrected_alignment["insertions"]),
            "classified_insertions": len(insertions),
            "complete": len(insertions) == corrected_alignment["insertions"],
        },
    }
    return {
        "schema_version": 1,
        "primary": primary,
        "allocation_summary": {
            "mode": allocation["allocation"]["allocation_mode"],
            "corrected_asr_words": allocation["corrected_asr_words"],
            "allocated_asr_words": allocation["allocation"]["allocated_asr_words"],
            "retained_live_words": allocation["allocation"]["retained_live_words"],
            "final_display_words": allocation["allocation"]["final_display_words"],
            "duplicate_corrected_asr_source_indices": sorted(
                allocation["duplicate_source_indices"]
            ),
            "unallocated_corrected_asr_source_indices": allocation["allocation"][
                "accounting"
            ]["unallocated_corrected_asr_source_indices"],
            "chunk_provenance_status": allocation["chunk_status"],
            "chunk_seam_indices": allocation["seam_indices"],
            "proven_chunk_seam_repeats": seam_matches,
        },
        "definitions": {
            "normalization": "transcript_alignment.normalize_words:[a-z']+",
            "standard_sid_tie_break": corrected_alignment["algorithm"]["tie_break"],
            "class_priority": list(CLASS_PRIORITY),
            "seam_rule": {
                "minimum_phrase_words": MIN_SEAM_PHRASE_WORDS,
                "maximum_phrase_words": MAX_SEAM_PHRASE_WORDS,
                "requires_observed_chunk_ranges": True,
                "marks_both_identical_sides": True,
            },
            "overlap_rule": (
                "only exact hypothesis identities affected by cross-channel "
                "TextGrid overlap order are ambiguous"
            ),
        },
    }


def build_report_from_paths(
    *,
    live_path: Path,
    corrected_path: Path,
    allocation_path: Path,
    doctor_path: Path,
    patient_path: Path,
    cutoff_seconds: float,
) -> dict[str, Any]:
    """Evaluate sealed paths and attach content hashes without exposing wording."""
    report = evaluate_artifacts(
        _load_json(live_path),
        _load_json(corrected_path),
        _load_json(allocation_path),
        doctor_path=doctor_path,
        patient_path=patient_path,
        cutoff_seconds=cutoff_seconds,
    )
    report["input_sha256"] = {
        "live": _sha256(live_path),
        "corrected": _sha256(corrected_path),
        "allocation": _sha256(allocation_path),
        "doctor_reference": _sha256(doctor_path),
        "patient_reference": _sha256(patient_path),
        "alignment_helper": _sha256(ALIGNMENT_MODULE_PATH),
    }
    report["cutoff_seconds"] = cutoff_seconds if math.isfinite(cutoff_seconds) else None
    return report


def _parser() -> argparse.ArgumentParser:
    """Return the command-line contract for a saved corrected fixture run."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", type=Path, required=True)
    parser.add_argument("--corrected", type=Path, required=True)
    parser.add_argument("--allocation", type=Path, required=True)
    parser.add_argument("--doctor", type=Path, required=True)
    parser.add_argument("--patient", type=Path, required=True)
    parser.add_argument(
        "--cutoff-seconds",
        type=float,
        default=math.inf,
        help="reference cutoff; default uses the complete TextGrid pair",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the PHI-safe machine-readable report",
    )
    return parser


def main() -> int:
    """Classify one saved run and print JSON or a compact count summary."""
    arguments = _parser().parse_args()
    try:
        report = build_report_from_paths(
            live_path=arguments.live,
            corrected_path=arguments.corrected,
            allocation_path=arguments.allocation,
            doctor_path=arguments.doctor,
            patient_path=arguments.patient,
            cutoff_seconds=arguments.cutoff_seconds,
        )
    except (OSError, ValueError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if arguments.json:
        print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
        return 0

    primary = report["primary"]
    live = primary["live"]
    corrected = primary["corrected"]
    print(
        f"live S/I/D={live['substitutions']}/{live['insertions']}/{live['deletions']}"
    )
    print(
        "corrected S/I/D="
        f"{corrected['substitutions']}/"
        f"{corrected['insertions']}/{corrected['deletions']}"
    )
    print(
        "classes "
        + " ".join(
            f"{class_name}={primary['class_counts'][class_name]}"
            for class_name in CLASS_NAMES
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
