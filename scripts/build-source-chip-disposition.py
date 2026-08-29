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
    """Stop a source-chip disposition that cannot be proved from saved evidence.

    The CLI turns this failure into one concise rejection and never prints partial JSON.
    Callers use it to distinguish rejected evidence from a completed disposition.
    """


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
    except json.JSONDecodeError as error:  # Example: the operator selected a score artifact truncated during capture.
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


def _validated_disposition_inputs(
    source_chip_score_path: Path,
    corrected_root_path: Path,
    repo_root_path: Path,
) -> tuple[Path, Path, Path, list[Any]]:
    """Resolve the saved score and same-run artifact root before any disposition work."""
    repo_root = repo_root_path.resolve()
    # A missing checkout boundary means the operator's evidence paths cannot be proven safe.
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
    # An empty score cannot support the complete disposition shown to an operator.
    if artifact_scores == []:
        raise _reject("source-chip score must contain at least one artifact")
    return repo_root, source_chip_score, corrected_root, artifact_scores


def _artifact_score_identity(
    artifact_score_value: object,
    repo_root: Path,
    corrected_root: Path,
) -> tuple[dict[str, Any], str, str, Path]:
    """Resolve one score entry to the corrected artifact selected by its fixture."""
    artifact_score = _object(artifact_score_value, "artifact score")
    artifact_path_text, fixture, corrected_artifact = _stored_artifact_path(
        repo_root,
        corrected_root,
        artifact_score.get("artifact_path"),
    )
    return artifact_score, artifact_path_text, fixture, corrected_artifact


def _load_aligned_artifact_rows(
    fixture: str,
    corrected_artifact: Path,
    artifact_score: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, Path]:
    """Load one fixture and prove its transcript, diagnostics, and saved row count agree."""
    diagnostics_path = corrected_artifact.with_name("corrected-row-diagnostics.json")
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
    # A stale score must not make the operator review a different set of transcript rows.
    if row_count != len(segments):
        raise _reject(f"{fixture} score row count disagrees")
    return segments, diagnostic_rows, row_count, diagnostics_path


def _finding_disposition_record(
    *,
    classification: str,
    diagnostic: dict[str, Any],
    end: float,
    finding_text: str,
    fixture: str,
    row_number: int,
    segment_id: str,
    start: float,
    visible_role: str,
) -> dict[str, Any]:
    """Return the text-free record an operator sees for one classified finding."""
    return {
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


def _finding_row_context(
    finding_value: object,
    fixture: str,
    artifact_path_text: str,
    segments: list[dict[str, Any]],
    diagnostic_rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], int, dict[str, Any], dict[str, Any], str]:
    """Join one saved finding to the corrected row the operator will review."""
    finding = _object(finding_value, f"{fixture} finding")
    # A finding from another saved artifact would mislabel evidence in the operator's report.
    if finding.get("artifact_path") != artifact_path_text:
        raise _reject(f"{fixture} finding artifact identity differs")

    row_number = _integer(
        finding.get("row_number"),
        f"{fixture} finding row number",
        minimum=1,
    )
    # The report cannot point the operator to a row that does not exist in the corrected transcript.
    if row_number > len(segments):
        raise _reject(f"{fixture} finding row is out of range")
    row_index = row_number - 1
    return (
        finding,
        row_number,
        segments[row_index],
        diagnostic_rows[row_index],
        f"{fixture} finding row {row_number}",
    )


def _validated_finding_identity(
    finding_value: object,
    fixture: str,
    artifact_path_text: str,
    segments: list[dict[str, Any]],
    diagnostic_rows: list[dict[str, Any]],
    seen_findings: set[tuple[str, int, str]],
) -> tuple[dict[str, Any], str, str]:
    """Validate, reserve, and classify one finding without retaining its wording."""
    finding, row_number, segment, diagnostic, label = _finding_row_context(
        finding_value,
        fixture,
        artifact_path_text,
        segments,
        diagnostic_rows,
    )

    segment_id = _string(
        finding.get("segment_id"),
        f"{label} segment ID",
    )
    visible_role = _role(finding.get("role"), f"{label} visible role")
    start = _number(finding.get("start"), f"{label} start")
    end = _number(finding.get("end"), f"{label} end")
    finding_text = finding.get("text")
    # Malformed wording has no safe fingerprint, so the finding cannot join to its saved row.
    if not isinstance(finding_text, str):
        raise _reject(f"{label} wording is malformed")

    # Every saved identity must still select the same row before its classification is trusted.
    if segment_id != segment.get("segment_id"):
        raise _reject(f"{label} segment identity differs")
    if visible_role != str(segment.get("role", "")).upper():
        raise _reject(f"{label} visible role differs")
    if start != float(segment.get("start")) or end != float(segment.get("end")):
        raise _reject(f"{label} timing differs")
    if _text_sha256(finding_text) != _text_sha256(segment["text"]):
        raise _reject(f"{label} wording fingerprint differs")

    severity = _string(finding.get("severity"), f"{label} severity")
    # Only the two scorer severities represented in the report can contribute to its totals.
    if severity not in {"error", "warning"}:
        raise _reject(f"{label} severity is unsupported")
    codes = _array(finding.get("codes"), f"{label} codes")
    # Missing or malformed codes would leave the reviewer without a stable reason for the alert.
    if codes == [] or any(
        not isinstance(code, str) or CODE_PATTERN.fullmatch(code) is None
        for code in codes
    ):
        raise _reject(f"{label} codes are malformed")

    identity = (fixture, row_number, segment_id)
    # Counting the same alert twice would make the final disposition appear more complete than its evidence.
    if identity in seen_findings:
        raise _reject(f"duplicate finding identity: {fixture} row {row_number}")
    seen_findings.add(identity)

    classification = _classification(diagnostic, label)
    return (
        _finding_disposition_record(
            classification=classification,
            diagnostic=diagnostic,
            end=end,
            finding_text=finding_text,
            fixture=fixture,
            row_number=row_number,
            segment_id=segment_id,
            start=start,
            visible_role=visible_role,
        ),
        severity,
        classification,
    )


def _validated_artifact_findings(
    artifact_score: dict[str, Any],
    artifact_path_text: str,
    fixture: str,
    segments: list[dict[str, Any]],
    diagnostic_rows: list[dict[str, Any]],
    seen_findings: set[tuple[str, int, str]],
) -> tuple[list[dict[str, Any]], Counter[str], int]:
    """Validate one fixture's finding totals and return its text-free classifications."""
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
    # Saved totals must close before individual findings can contribute to the operator's disposition.
    if finding_count != len(findings):
        raise _reject(f"{fixture} score finding count disagrees")
    if error_count + warning_count != finding_count:
        raise _reject(f"{fixture} score severity counts disagree")

    observed_severities = Counter()
    classification_counts = Counter({name: 0 for name in CLASSIFICATIONS})
    finding_identities: list[dict[str, Any]] = []
    for finding_value in findings:
        finding_identity, severity, classification = _validated_finding_identity(
            finding_value,
            fixture,
            artifact_path_text,
            segments,
            diagnostic_rows,
            seen_findings,
        )
        observed_severities[severity] += 1
        classification_counts[classification] += 1
        finding_identities.append(finding_identity)

    # A severity mismatch means the score header and the alerts shown to the reviewer describe different evidence.
    if observed_severities["error"] != error_count:
        raise _reject(f"{fixture} score error count disagrees")
    if observed_severities["warning"] != warning_count:
        raise _reject(f"{fixture} score warning count disagrees")
    return finding_identities, classification_counts, finding_count


def _build_disposition_document(
    source_chip_score: Path,
    artifact_identities: list[dict[str, Any]],
    finding_identities: list[dict[str, Any]],
    classification_counts: Counter[str],
    total_rows: int,
) -> dict[str, Any]:
    """Assemble the byte-stable report after every saved artifact passes validation."""
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
    # Operators must never receive a complete status while any saved finding lacks a disposition.
    if document["counts"]["classified_finding_count"] != total_findings:
        raise _reject("classified finding count is incomplete")
    _assert_text_free_document(document)
    return document


def build_source_chip_disposition(
    source_chip_score_path: Path,
    corrected_root_path: Path,
    repo_root_path: Path,
) -> dict[str, Any]:
    """Build the text-free disposition operators use to review frozen score evidence.

    :param source_chip_score_path: Saved score JSON; a missing or empty score produces no report.
    :param corrected_root_path: Same-run corrected artifact root; a missing directory fails closed.
    :param repo_root_path: Checkout boundary used to reject paths outside the repository.
    :returns: A complete disposition; its findings list may be empty when the score contains no alerts.
    :raises M05SourceChipDispositionError: If evidence is missing, malformed, misbound, or incomplete.
    :raises OSError: If a validated input becomes unreadable while it is being hashed or loaded.
    """
    repo_root, source_chip_score, corrected_root, artifact_scores = (
        _validated_disposition_inputs(
            source_chip_score_path,
            corrected_root_path,
            repo_root_path,
        )
    )

    artifact_identities: list[dict[str, Any]] = []
    finding_identities: list[dict[str, Any]] = []
    classification_counts = Counter({name: 0 for name in CLASSIFICATIONS})
    seen_fixtures: set[str] = set()
    seen_findings: set[tuple[str, int, str]] = set()
    total_rows = 0

    for artifact_score_value in artifact_scores:
        artifact_score, artifact_path_text, fixture, corrected_artifact = (
            _artifact_score_identity(
                artifact_score_value,
                repo_root,
                corrected_root,
            )
        )
        # A repeated fixture could make one visit count twice in the operator's final totals.
        if fixture in seen_fixtures:
            raise _reject(f"duplicate fixture identity: {fixture}")
        seen_fixtures.add(fixture)

        segments, diagnostic_rows, row_count, diagnostics_path = (
            _load_aligned_artifact_rows(
                fixture,
                corrected_artifact,
                artifact_score,
            )
        )
        artifact_findings, artifact_classifications, finding_count = (
            _validated_artifact_findings(
                artifact_score,
                artifact_path_text,
                fixture,
                segments,
                diagnostic_rows,
                seen_findings,
            )
        )
        finding_identities.extend(artifact_findings)
        classification_counts.update(artifact_classifications)
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

    return _build_disposition_document(
        source_chip_score,
        artifact_identities,
        finding_identities,
        classification_counts,
        total_rows,
    )


def render_source_chip_disposition(document: dict[str, Any]) -> str:
    """Render one complete disposition for CLI output or a saved review artifact.

    :param document: Text-free disposition to render; forbidden wording fields are rejected.
    :returns: One compact, newline-terminated JSON record; never an empty string.
    :raises M05SourceChipDispositionError: If the document could expose transcript wording.
    """
    _assert_text_free_document(document)
    return json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"


def parse_args() -> argparse.Namespace:
    """Parse the frozen source score and same-run artifact root.

    :returns: Validated CLI arguments; required evidence paths are never empty.
    """
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
    """Build a disposition or return one fail-closed diagnostic.

    :returns: Zero after printing a complete report, or one when selected evidence is rejected.
    """
    arguments = parse_args()
    try:
        document = build_source_chip_disposition(
            arguments.source_chip_score,
            arguments.corrected_root,
            arguments.repo_root,
        )
        sys.stdout.write(render_source_chip_disposition(document))
    except (M05SourceChipDispositionError, OSError) as error:
        # Example: a moved score file gives the operator one rejection and no partial JSON document.
        print(f"source-chip disposition rejected: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
