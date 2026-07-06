"""
Corrected source-chip scoring for post-visit transcript QA.

This helper reads corrected transcript rows after a stopped visit and reports
rows whose Doctor/Patient label conflicts with high-confidence text cues. Use
it from fixture scripts before adding role cleanup logic, so the user-visible
summary evidence stays reviewable without changing runtime labels.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SUPPORTED_SOURCE_CHIP_ROLES = {"DOCTOR", "PATIENT"}
_SHORT_PATIENT_ACKNOWLEDGEMENTS = {
    "yeah",
    "yes",
    "yep",
    "no",
    "nope",
    "um no",
    "uh no",
}

_DOCTOR_QUESTION_PATTERNS = (
    re.compile(
        r"\b(can|could|did|do|does|have|has|are|is|was|were)\s+you\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(tell me|describe|any temperatures|any fevers)\b", re.IGNORECASE),
    re.compile(r"\b(your neck|bright lights|vomited at all)\b", re.IGNORECASE),
)
_DOCTOR_CARE_PATTERNS = (
    re.compile(r"\b(i'?m sorry to hear|i can understand)\b", re.IGNORECASE),
    re.compile(r"\b(let me examine|we can have a little chat)\b", re.IGNORECASE),
)
_PATIENT_FIRST_PERSON_PATTERNS = (
    re.compile(
        r"\b(i('| a)?m\s+(wearing|worried|really|just|ill|unwell|concerned|\d+)|"
        r"i have|i've|i had|i feel|i felt|i guess|"
        r"i don't|i do not|i just|i need|i vomited|like if i|when i|if i)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(my|my friend's mum)\b", re.IGNORECASE),
)
_PATIENT_SYMPTOM_PATTERNS = (
    re.compile(
        r"\b(headache|throbbing|vomit|vomited|nauseous|sunglasses|"
        r"lights? really hurting|feverish|worried|googling|brain cancer)\b",
        re.IGNORECASE,
    ),
)


@dataclass(frozen=True)
class SourceChipFinding:
    """
    One corrected row that should be reviewed before trusting summary evidence.

    The scorer creates this when a source chip's role label and text cues point
    in different directions. Use it in QA reports, not as automatic relabeling.

    Attributes:
        artifact_path: Corrected transcript JSON path shown in the QA report.
        row_number: One-based row number matching the visible source-chip order.
        segment_id: Corrected row ID; empty input is replaced with a row label.
        role: Current Doctor/Patient label shown for that source chip.
        start: Row start time in seconds; `0.0` means missing timing.
        end: Row end time in seconds; `0.0` means missing timing.
        text: Source-chip text the clinician may inspect.
        severity: `error` for direct cue conflict, `warning` for context-only review.
        codes: Stable cue codes for scripts and changelog evidence.
        evidence: Plain-English cue labels that explain why the row was flagged.
    """

    artifact_path: str
    row_number: int
    segment_id: str
    role: str
    start: float
    end: float
    text: str
    severity: str
    codes: tuple[str, ...]
    evidence: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe finding for saved QA reports.

        Returns:
            Dictionary representation; empty code/evidence tuples become lists.
        """
        return {
            "artifact_path": self.artifact_path,
            "row_number": self.row_number,
            "segment_id": self.segment_id,
            "role": self.role,
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "severity": self.severity,
            "codes": list(self.codes),
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class SourceChipArtifactScore:
    """
    Review summary for one corrected transcript artifact.

    A fixture run can aggregate these scores across consultations to decide
    whether the user-facing corrected summary lane needs more cleanup work.

    Attributes:
        artifact_path: Corrected transcript JSON path or test label.
        row_count: Number of corrected rows inspected; `0` means no source chips.
        findings: Row-level review findings for this artifact.
    """

    artifact_path: str
    row_count: int
    findings: tuple[SourceChipFinding, ...]

    @property
    def finding_count(self) -> int:
        """Return the number of rows that need source-chip review.

        Returns:
            Finding count; `0` means this artifact has no flagged rows.
        """
        return len(self.findings)

    @property
    def error_count(self) -> int:
        """Return findings with direct role-cue contradictions.

        Returns:
            Error count; `0` means no direct Doctor/Patient cue conflict.
        """
        # Each finding is one row the QA report will show to a developer.
        return sum(1 for finding in self.findings if finding.severity == "error")

    @property
    def warning_count(self) -> int:
        """Return contextual findings that need review but are less certain.

        Returns:
            Warning count; `0` means no context-only review rows.
        """
        # Warnings are useful for tiny acknowledgements where text alone is weak.
        return sum(1 for finding in self.findings if finding.severity == "warning")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe artifact score for local QA output.

        Returns:
            Dictionary summary with nested row findings for saved evidence.
        """
        return {
            "artifact_path": self.artifact_path,
            "row_count": self.row_count,
            "finding_count": self.finding_count,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "findings": [finding.to_dict() for finding in self.findings],
        }


def load_corrected_segments(artifact_path: Path) -> list[dict[str, Any]]:
    """Read corrected transcript rows from a FastAPI QA artifact.

    Args:
        artifact_path: JSON file from `/corrected-transcript`; missing `segments`
            means the artifact cannot be scored.

    Returns:
        Corrected rows; empty means the artifact exists but has no scoreable rows.

    Raises:
        ValueError: When `segments` is not a list and cannot represent source chips.
        json.JSONDecodeError: When the file is not valid JSON.
    """
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    segments = payload.get("segments", [])

    # Non-list segments would make row numbers and source chips meaningless.
    if not isinstance(segments, list):
        raise ValueError(f"{artifact_path} does not contain a segment list")

    return [segment for segment in segments if isinstance(segment, dict)]


def find_corrected_transcript_artifacts(paths: list[Path]) -> list[Path]:
    """Resolve files or fixture directories to corrected transcript JSON files.

    Args:
        paths: Files or directories selected by the developer; empty is invalid.

    Returns:
        Unique artifact files in stable path order.

    Raises:
        FileNotFoundError: When a selected file or directory does not exist.
    """
    found_paths: list[Path] = []

    # Each input can be a direct QA JSON file or a fixture-run directory.
    for selected_path in paths:
        # Direct files let manual browser artifacts be scored without a run dir.
        if selected_path.is_file():
            found_paths.append(selected_path)
            continue

        # Missing paths are reported clearly instead of silently scoring nothing.
        if not selected_path.exists():
            raise FileNotFoundError(selected_path)

        # Fixture-run directories contain one corrected transcript per consultation.
        for artifact_path in selected_path.rglob("corrected-transcript.json"):
            found_paths.append(artifact_path)

    return sorted({path.resolve() for path in found_paths})


def score_corrected_segments(
    corrected_segments: list[dict[str, Any]],
    artifact_path: str = "",
) -> SourceChipArtifactScore:
    """Score corrected rows for Doctor/Patient source-chip contradictions.

    Args:
        corrected_segments: Rows from a corrected transcript artifact; empty
            means the user has no corrected source chips to score.
        artifact_path: Report label for the artifact; empty is allowed in tests.

    Returns:
        Artifact score with row-level findings for QA review.
    """
    findings: list[SourceChipFinding] = []
    previous_segment: dict[str, Any] | None = None

    # Rows are scored in visible order because tiny acknowledgements need context.
    for row_number, segment in enumerate(corrected_segments, start=1):
        finding = score_corrected_segment(
            segment=segment,
            previous_segment=previous_segment,
            row_number=row_number,
            artifact_path=artifact_path,
        )

        # Clean rows do not appear in the QA review list.
        if finding is not None:
            findings.append(finding)

        previous_segment = segment

    return SourceChipArtifactScore(
        artifact_path=artifact_path,
        row_count=len(corrected_segments),
        findings=tuple(findings),
    )


def score_corrected_artifact(artifact_path: Path) -> SourceChipArtifactScore:
    """Load and score one corrected transcript JSON artifact.

    Args:
        artifact_path: Corrected transcript JSON file selected by the developer.

    Returns:
        Source-chip score for the artifact; empty rows produce zero findings.

    Raises:
        ValueError: When the artifact shape cannot represent corrected rows.
        json.JSONDecodeError: When the artifact is not valid JSON.
    """
    return score_corrected_segments(
        load_corrected_segments(artifact_path),
        artifact_path=str(artifact_path),
    )


def score_corrected_segment(
    segment: dict[str, Any],
    previous_segment: dict[str, Any] | None,
    row_number: int,
    artifact_path: str,
) -> SourceChipFinding | None:
    """Return a review finding when one corrected row contradicts its role.

    Args:
        segment: Corrected row from the QA artifact; empty text has no cue evidence.
        previous_segment: Previous corrected row; `None` means no context cue.
        row_number: One-based row number shown in the QA report.
        artifact_path: Artifact label printed with the finding; empty in tests.

    Returns:
        Review finding, or `None` when the row has no contradiction cue.
    """
    role = str(segment.get("role", "")).upper()

    # Unknown or non-clinical labels are not Doctor/Patient contradictions.
    if role not in SUPPORTED_SOURCE_CHIP_ROLES:
        return None

    text = str(segment.get("text", ""))
    normalized_text = normalize_source_text(text)
    doctor_cues = doctor_cue_labels(normalized_text)
    patient_cues = patient_cue_labels(normalized_text)
    codes: list[str] = []
    evidence: list[str] = []

    # Patient rows that contain doctor prompts can mislead summary evidence.
    if role == "PATIENT" and doctor_cues:
        codes.append("doctor_question_in_patient_row")
        evidence.extend(doctor_cues)

    # Doctor rows that contain first-person symptom text likely borrowed patient words.
    if role == "DOCTOR" and patient_cues:
        codes.append("patient_statement_in_doctor_row")
        evidence.extend(patient_cues)

    # Mixed rows are especially risky as clickable summary source chips.
    if doctor_cues and patient_cues:
        codes.append("mixed_role_cues")

    # A tiny "yeah/no" after a doctor question is often the patient's answer.
    if role == "DOCTOR" and is_patient_ack_after_doctor_question(
        normalized_text,
        previous_segment,
    ):
        codes.append("patient_ack_after_doctor_question")
        evidence.append("short acknowledgement after doctor question")

    # Rows without contradiction evidence stay out of the report.
    if not codes:
        return None

    return SourceChipFinding(
        artifact_path=artifact_path,
        row_number=row_number,
        segment_id=str(segment.get("segment_id", f"row-{row_number:04d}")),
        role=role,
        start=float(segment.get("start", 0.0) or 0.0),
        end=float(segment.get("end", 0.0) or 0.0),
        text=text,
        severity=finding_severity(codes),
        codes=tuple(dict.fromkeys(codes)),
        evidence=tuple(dict.fromkeys(evidence)),
    )


def doctor_cue_labels(normalized_text: str) -> list[str]:
    """Return doctor cue labels found in a corrected source row.

    Args:
        normalized_text: Lowercase text; empty means no clinician cue can match.

    Returns:
        Cue labels; empty means no doctor-owned wording was found.
    """
    labels: list[str] = []

    # Question prompts are the strongest sign a row belongs to the clinician.
    for pattern in _DOCTOR_QUESTION_PATTERNS:
        # Each pattern match gives the QA report a plain-English cue family.
        if pattern.search(normalized_text):
            labels.append("doctor question prompt")
            break

    # Empathy/exam phrases are doctor-owned even when they include first person.
    for pattern in _DOCTOR_CARE_PATTERNS:
        # Care-plan phrases help flag doctor text under a patient chip.
        if pattern.search(normalized_text):
            labels.append("doctor care or exam phrase")
            break

    return labels


def patient_cue_labels(normalized_text: str) -> list[str]:
    """Return patient cue labels found in a corrected source row.

    Args:
        normalized_text: Lowercase text; empty means no patient cue can match.

    Returns:
        Cue labels; empty means no patient-owned wording was found.
    """
    labels: list[str] = []
    has_first_person_cue = False

    # First-person symptom wording is the strongest sign a row belongs to the patient.
    for pattern in _PATIENT_FIRST_PERSON_PATTERNS:
        # One first-person match is enough; repeated words do not add signal.
        if pattern.search(normalized_text):
            labels.append("patient first-person statement")
            has_first_person_cue = True
            break

    # Symptom and concern terms support review when paired with first-person wording.
    if has_first_person_cue:
        # Symptom terms alone also appear in valid doctor questions and summaries.
        for pattern in _PATIENT_SYMPTOM_PATTERNS:
            # One symptom-family match is enough for row-level QA evidence.
            if pattern.search(normalized_text):
                labels.append("patient symptom or concern term")
                break

    return labels


def is_patient_ack_after_doctor_question(
    normalized_text: str,
    previous_segment: dict[str, Any] | None,
) -> bool:
    """Return whether a tiny answer likely belongs to the patient.

    Args:
        normalized_text: Current row text after punctuation cleanup; empty means
            no source-chip text can be judged.
        previous_segment: Previous corrected row; `None` means no question context.

    Returns:
        True when a short acknowledgement follows a doctor question.
    """
    # Longer rows need lexical cue scoring instead of context-only judgement.
    if normalized_text not in _SHORT_PATIENT_ACKNOWLEDGEMENTS:
        return False

    # The first row has no previous doctor prompt for the user to compare.
    if previous_segment is None:
        return False

    previous_role = str(previous_segment.get("role", "")).upper()
    previous_text = normalize_source_text(str(previous_segment.get("text", "")))

    return previous_role == "DOCTOR" and doctor_cue_labels(previous_text) != []


def finding_severity(codes: list[str]) -> str:
    """Return report severity for a row's cue codes.

    Args:
        codes: Cue codes for one row; empty should not reach this helper.

    Returns:
        `warning` for context-only rows, otherwise `error`.
    """
    # Context-only acknowledgements are weaker than direct lexical contradictions.
    if codes == ["patient_ack_after_doctor_question"]:
        return "warning"
    return "error"


def normalize_source_text(text: str) -> str:
    """Return lowercase source text with punctuation collapsed for cue checks.

    Args:
        text: Source-chip text; empty stays empty after normalization.

    Returns:
        Normalized text ready for regex cue checks.
    """
    normalized = re.sub(r"[^a-z0-9']+", " ", text.lower()).strip()
    return re.sub(r"\s+", " ", normalized)


def format_source_time(seconds: float) -> str:
    """Format a source-chip timestamp for terminal QA reports.

    Args:
        seconds: Source time in seconds; negative values display as `00:00`.

    Returns:
        `MM:SS` timestamp matching the summary source-chip UI.
    """
    safe_seconds = max(0, int(seconds))
    minutes, remaining_seconds = divmod(safe_seconds, 60)
    return f"{minutes:02d}:{remaining_seconds:02d}"


def build_text_report(scores: list[SourceChipArtifactScore]) -> str:
    """Build the human-readable corpus report shown by the CLI.

    Args:
        scores: Artifact scores to print; empty means no artifacts were selected.

    Returns:
        Terminal report with artifact counts and row-level findings.
    """
    total_rows = sum(score.row_count for score in scores)
    total_findings = sum(score.finding_count for score in scores)
    total_errors = sum(score.error_count for score in scores)
    total_warnings = sum(score.warning_count for score in scores)
    lines = [
        "Corrected source-chip scorer",
        (
            f"artifacts={len(scores)} rows={total_rows} findings={total_findings} "
            f"errors={total_errors} warnings={total_warnings}"
        ),
        "",
        "Artifacts",
    ]

    # Each artifact row lets the developer pick the noisy fixture first.
    for score in scores:
        lines.append(
            (
                f"- {score.artifact_path}: rows={score.row_count} "
                f"findings={score.finding_count} errors={score.error_count} "
                f"warnings={score.warning_count}"
            )
        )

    lines.append("")
    lines.append("Findings")

    # Empty findings are a useful pass signal for a fixture run.
    if total_findings == 0:
        lines.append("- none")
        return "\n".join(lines)

    # Findings include row identity and cues so the source chip can be inspected.
    for score in scores:
        # Clean artifacts do not need row-level output.
        if score.finding_count == 0:
            continue

        lines.append(f"- {score.artifact_path}")
        # Each row finding is a concrete source chip the user may review.
        for finding in score.findings:
            lines.append(
                (
                    f"  {finding.segment_id} row={finding.row_number} "
                    f"{finding.role} {format_source_time(finding.start)}-"
                    f"{format_source_time(finding.end)} "
                    f"{finding.severity} codes={','.join(finding.codes)}"
                )
            )
            lines.append(f"    text={finding.text}")
            lines.append(f"    evidence={'; '.join(finding.evidence)}")

    return "\n".join(lines)
