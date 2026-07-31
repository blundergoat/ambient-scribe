#!/usr/bin/env python3
"""Build a deterministic, text-free source-chip disposition.

The evaluator consumes one saved source-chip score plus the same-run corrected
transcript and corrected-row diagnostics for each fixture. It performs no
runtime, network, model, correction, or GPU work.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "ambient-scribe-m05-source-chip-disposition/v1"
CLASSIFICATIONS = (
    "truth_aligned_heuristic_false_positive",
    "baseline_confirmed_role_error",
    "reference_gap",
)
SUPPORTED_ROLES = frozenset({"DOCTOR", "PATIENT"})
FORBIDDEN_OUTPUT_KEYS = frozenset(
    {
        "content",
        "decoded_text",
        "evidence",
        "raw_text",
        "text",
        "transcript",
        "utterance",
        "word",
        "words",
    }
)
FIXTURE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
CODE_PATTERN = re.compile(r"[a-z0-9_]+")


class M05SourceChipDispositionError(ValueError):
    """Raised when source-chip evidence cannot be classified completely."""


def _reject(detail: str) -> M05SourceChipDispositionError:
    """Return one stable fail-closed evidence error."""
    return M05SourceChipDispositionError(detail)


def _file_sha256(path: Path) -> str:
    """Return the SHA-256 identity of one frozen input file."""
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for block in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _text_sha256(value: str) -> str:
    """Return an exact UTF-8 fingerprint without retaining wording."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _load_json(path: Path, label: str) -> Any:
    """Load one required JSON input with a bounded error label."""
    if not path.is_file():
        raise _reject(f"{label} is missing")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise _reject(f"{label} is malformed JSON") from error


def _confined_path(
    repo_root: Path,
    selected_path: Path,
    *,
    label: str,
    require_file: bool = False,
    require_directory: bool = False,
) -> Path:
    """Resolve one selected path without allowing checkout escape."""
    candidate = selected_path
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    resolved = candidate.resolve()
    if not resolved.is_relative_to(repo_root):
        raise _reject(f"{label} escapes the repository")
    if require_file and not resolved.is_file():
        raise _reject(f"{label} is missing")
    if require_directory and not resolved.is_dir():
        raise _reject(f"{label} is missing")
    return resolved


def _object(value: object, label: str) -> dict[str, Any]:
    """Require one JSON object."""
    if not isinstance(value, dict):
        raise _reject(f"{label} must be an object")
    return value


def _array(value: object, label: str) -> list[Any]:
    """Require one JSON array."""
    if not isinstance(value, list):
        raise _reject(f"{label} must be an array")
    return value


def _string(value: object, label: str) -> str:
    """Require one non-empty string identity."""
    if not isinstance(value, str) or value == "":
        raise _reject(f"{label} must be a non-empty string")
    return value


def _integer(value: object, label: str, *, minimum: int = 0) -> int:
    """Require one bounded integer count or index."""
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _reject(f"{label} must be an integer >= {minimum}")
    return value


def _number(value: object, label: str) -> float:
    """Require one finite numeric timing identity."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _reject(f"{label} must be a finite number")
    numeric_value = float(value)
    if not math.isfinite(numeric_value):
        raise _reject(f"{label} must be a finite number")
    return numeric_value


def _role(value: object, label: str) -> str:
    """Require one supported visible role."""
    role = _string(value, label).upper()
    if role not in SUPPORTED_ROLES:
        raise _reject(f"{label} is not DOCTOR or PATIENT")
    return role


def _classification(diagnostic: dict[str, Any], label: str) -> str:
    """Classify one aligned finding from its reference diagnostic."""
    correct = diagnostic.get("correct")
    confidently_wrong = diagnostic.get("confidently_wrong")
    expected_role_value = diagnostic.get("expected_role")
    visible_role = _role(diagnostic.get("visible_role"), f"{label} visible role")

    if not isinstance(confidently_wrong, bool):
        raise _reject(f"{label} confidently_wrong must be boolean")

    if correct is True:
        expected_role = _role(expected_role_value, f"{label} expected role")
        if confidently_wrong or expected_role != visible_role:
            raise _reject(f"{label} has inconsistent truth-aligned diagnostics")
        return "truth_aligned_heuristic_false_positive"

    if correct is False:
        expected_role = _role(expected_role_value, f"{label} expected role")
        if not confidently_wrong or expected_role == visible_role:
            raise _reject(f"{label} has inconsistent confidently-wrong diagnostics")
        return "baseline_confirmed_role_error"

    if correct is None:
        if confidently_wrong or expected_role_value is not None:
            raise _reject(f"{label} has inconsistent reference-gap diagnostics")
        return "reference_gap"

    raise _reject(f"{label} has no supported classification")


def _assert_text_free_document(value: object, path: str = "$") -> None:
    """Reject any output field that could carry transcript wording."""
    if isinstance(value, dict):
        for key, nested_value in value.items():
            if key in FORBIDDEN_OUTPUT_KEYS:
                raise _reject(f"output contains forbidden field {path}.{key}")
            _assert_text_free_document(nested_value, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested_value in enumerate(value):
            _assert_text_free_document(nested_value, f"{path}[{index}]")


def _stored_artifact_path(
    repo_root: Path,
    corrected_root: Path,
    artifact_path_value: object,
) -> tuple[str, str, Path]:
    """Resolve one score-owned corrected artifact to its fixture directory."""
    artifact_path_text = _string(artifact_path_value, "artifact path")
    artifact_path = Path(artifact_path_text)
    if artifact_path.name != "corrected-transcript.json":
        raise _reject("artifact path must end in corrected-transcript.json")

    fixture = artifact_path.parent.name
    if FIXTURE_PATTERN.fullmatch(fixture) is None:
        raise _reject("fixture identity is malformed")

    resolved_artifact = _confined_path(
        repo_root,
        artifact_path,
        label=f"{fixture} corrected artifact",
        require_file=True,
    )
    expected_artifact = (
        corrected_root / fixture / "corrected-transcript.json"
    ).resolve()
    if resolved_artifact != expected_artifact:
        raise _reject(f"{fixture} artifact path does not match corrected root")
    return artifact_path_text, fixture, resolved_artifact


def _validate_aligned_rows(
    fixture: str,
    segments: list[Any],
    diagnostic_rows: list[Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate complete row alignment before classifying any finding."""
    if len(segments) != len(diagnostic_rows):
        raise _reject(f"{fixture} transcript and diagnostic row counts differ")

    validated_segments: list[dict[str, Any]] = []
    validated_diagnostics: list[dict[str, Any]] = []
    segment_ids: set[str] = set()
    for row_index, (segment_value, diagnostic_value) in enumerate(
        zip(segments, diagnostic_rows, strict=True)
    ):
        row_number = row_index + 1
        label = f"{fixture} row {row_number}"
        segment = _object(segment_value, f"{label} segment")
        diagnostic = _object(diagnostic_value, f"{label} diagnostic")

        segment_id = _string(segment.get("segment_id"), f"{label} segment ID")
        if segment_id in segment_ids:
            raise _reject(f"{fixture} contains duplicate segment identity")
        segment_ids.add(segment_id)

        segment_role = _string(
            segment.get("role"),
            f"{label} segment role",
        ).upper()
        segment_start = _number(segment.get("start"), f"{label} segment start")
        segment_end = _number(segment.get("end"), f"{label} segment end")
        diagnostic_index = _integer(
            diagnostic.get("row_index"),
            f"{label} diagnostic row index",
        )
        diagnostic_role = _string(
            diagnostic.get("visible_role"),
            f"{label} diagnostic role",
        ).upper()
        diagnostic_start = _number(
            diagnostic.get("start"),
            f"{label} diagnostic start",
        )
        diagnostic_end = _number(
            diagnostic.get("end"),
            f"{label} diagnostic end",
        )

        if diagnostic_index != row_index:
            raise _reject(f"{label} diagnostic row identity differs")
        if diagnostic_role != segment_role:
            raise _reject(f"{label} diagnostic visible role differs")
        if diagnostic_start != segment_start or diagnostic_end != segment_end:
            raise _reject(f"{label} diagnostic timing differs")
        if not isinstance(segment.get("text"), str):
            raise _reject(f"{label} segment wording is malformed")

        validated_segments.append(segment)
        validated_diagnostics.append(diagnostic)

    return validated_segments, validated_diagnostics


def build_source_chip_disposition(
    source_chip_score_path: Path,
    corrected_root_path: Path,
    repo_root_path: Path,
) -> dict[str, Any]:
    """Build one complete disposition from frozen same-run artifacts."""
    repo_root = repo_root_path.resolve()
    if not repo_root.is_dir():
        raise _reject("repository root is missing")
    source_chip_score = _confined_path(
        repo_root,
        source_chip_score_path,
        label="source-chip score",
        require_file=True,
    )
    corrected_root = _confined_path(
        repo_root,
        corrected_root_path,
        label="corrected root",
        require_directory=True,
    )
    artifact_scores = _array(
        _load_json(source_chip_score, "source-chip score"),
        "source-chip score",
    )
    if artifact_scores == []:
        raise _reject("source-chip score must contain at least one artifact")

    artifact_identities: list[dict[str, Any]] = []
    finding_identities: list[dict[str, Any]] = []
    classification_counts = Counter({name: 0 for name in CLASSIFICATIONS})
    seen_fixtures: set[str] = set()
    seen_findings: set[tuple[str, int, str]] = set()
    total_rows = 0

    for artifact_score_value in artifact_scores:
        artifact_score = _object(artifact_score_value, "artifact score")
        artifact_path_text, fixture, corrected_artifact = _stored_artifact_path(
            repo_root,
            corrected_root,
            artifact_score.get("artifact_path"),
        )
        if fixture in seen_fixtures:
            raise _reject(f"duplicate fixture identity: {fixture}")
        seen_fixtures.add(fixture)

        diagnostics_path = corrected_artifact.with_name(
            "corrected-row-diagnostics.json"
        )
        transcript_document = _object(
            _load_json(corrected_artifact, f"{fixture} corrected artifact"),
            f"{fixture} corrected artifact",
        )
        diagnostics_document = _object(
            _load_json(diagnostics_path, f"{fixture} corrected diagnostics"),
            f"{fixture} corrected diagnostics",
        )
        segments, diagnostic_rows = _validate_aligned_rows(
            fixture,
            _array(
                transcript_document.get("segments"),
                f"{fixture} corrected segments",
            ),
            _array(
                diagnostics_document.get("rows"),
                f"{fixture} diagnostic rows",
            ),
        )
        row_count = _integer(
            artifact_score.get("row_count"),
            f"{fixture} score row count",
        )
        if row_count != len(segments):
            raise _reject(f"{fixture} score row count disagrees")

        findings = _array(
            artifact_score.get("findings"),
            f"{fixture} score findings",
        )
        finding_count = _integer(
            artifact_score.get("finding_count"),
            f"{fixture} score finding count",
        )
        error_count = _integer(
            artifact_score.get("error_count"),
            f"{fixture} score error count",
        )
        warning_count = _integer(
            artifact_score.get("warning_count"),
            f"{fixture} score warning count",
        )
        if finding_count != len(findings):
            raise _reject(f"{fixture} score finding count disagrees")
        if error_count + warning_count != finding_count:
            raise _reject(f"{fixture} score severity counts disagree")

        observed_severities = Counter()
        for finding_value in findings:
            finding = _object(finding_value, f"{fixture} finding")
            if finding.get("artifact_path") != artifact_path_text:
                raise _reject(f"{fixture} finding artifact identity differs")

            row_number = _integer(
                finding.get("row_number"),
                f"{fixture} finding row number",
                minimum=1,
            )
            if row_number > len(segments):
                raise _reject(f"{fixture} finding row is out of range")
            row_index = row_number - 1
            segment = segments[row_index]
            diagnostic = diagnostic_rows[row_index]
            label = f"{fixture} finding row {row_number}"

            segment_id = _string(
                finding.get("segment_id"),
                f"{label} segment ID",
            )
            visible_role = _role(finding.get("role"), f"{label} visible role")
            start = _number(finding.get("start"), f"{label} start")
            end = _number(finding.get("end"), f"{label} end")
            finding_text = finding.get("text")
            if not isinstance(finding_text, str):
                raise _reject(f"{label} wording is malformed")

            if segment_id != segment.get("segment_id"):
                raise _reject(f"{label} segment identity differs")
            if visible_role != str(segment.get("role", "")).upper():
                raise _reject(f"{label} visible role differs")
            if start != float(segment.get("start")) or end != float(segment.get("end")):
                raise _reject(f"{label} timing differs")
            if _text_sha256(finding_text) != _text_sha256(segment["text"]):
                raise _reject(f"{label} wording fingerprint differs")

            severity = _string(finding.get("severity"), f"{label} severity")
            if severity not in {"error", "warning"}:
                raise _reject(f"{label} severity is unsupported")
            observed_severities[severity] += 1
            codes = _array(finding.get("codes"), f"{label} codes")
            if codes == [] or any(
                not isinstance(code, str)
                or CODE_PATTERN.fullmatch(code) is None
                for code in codes
            ):
                raise _reject(f"{label} codes are malformed")

            identity = (fixture, row_number, segment_id)
            if identity in seen_findings:
                raise _reject(f"duplicate finding identity: {fixture} row {row_number}")
            seen_findings.add(identity)

            classification = _classification(diagnostic, label)
            classification_counts[classification] += 1
            finding_identities.append(
                {
                    "classification": classification,
                    "end": end,
                    "expected_role": diagnostic.get("expected_role"),
                    "fixture": fixture,
                    "row_number": row_number,
                    "segment_id": segment_id,
                    "start": start,
                    "text_sha256": _text_sha256(finding_text),
                    "visible_role": visible_role,
                }
            )

        if observed_severities["error"] != error_count:
            raise _reject(f"{fixture} score error count disagrees")
        if observed_severities["warning"] != warning_count:
            raise _reject(f"{fixture} score warning count disagrees")

        total_rows += row_count
        artifact_identities.append(
            {
                "corrected_artifact_sha256": _file_sha256(corrected_artifact),
                "corrected_diagnostics_sha256": _file_sha256(diagnostics_path),
                "finding_count": finding_count,
                "fixture": fixture,
                "row_count": row_count,
            }
        )

    artifact_identities.sort(key=lambda item: item["fixture"])
    finding_identities.sort(
        key=lambda item: (
            item["fixture"],
            item["row_number"],
            item["segment_id"],
        )
    )
    total_findings = len(finding_identities)
    document: dict[str, Any] = {
        "artifacts": artifact_identities,
        "counts": {
            "alignment_mismatch_count": 0,
            "artifact_count": len(artifact_identities),
            "classification_counts": {
                name: classification_counts[name] for name in CLASSIFICATIONS
            },
            "classified_finding_count": sum(classification_counts.values()),
            "finding_count": total_findings,
            "row_count": total_rows,
            "unclassified_finding_count": 0,
        },
        "findings": finding_identities,
        "schema_version": SCHEMA_VERSION,
        "source_chip_score_sha256": _file_sha256(source_chip_score),
        "status": "complete",
    }
    if document["counts"]["classified_finding_count"] != total_findings:
        raise _reject("classified finding count is incomplete")
    _assert_text_free_document(document)
    return document


def render_source_chip_disposition(document: dict[str, Any]) -> str:
    """Render one byte-stable JSON result."""
    _assert_text_free_document(document)
    return json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"


def parse_args() -> argparse.Namespace:
    """Parse the frozen source score and same-run artifact root."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-chip-score", required=True, type=Path)
    parser.add_argument("--corrected-root", required=True, type=Path)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    return parser.parse_args()


def main() -> int:
    """Build a disposition or return one fail-closed diagnostic."""
    arguments = parse_args()
    try:
        document = build_source_chip_disposition(
            arguments.source_chip_score,
            arguments.corrected_root,
            arguments.repo_root,
        )
        sys.stdout.write(render_source_chip_disposition(document))
    except (M05SourceChipDispositionError, OSError) as error:
        print(f"source-chip disposition rejected: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
