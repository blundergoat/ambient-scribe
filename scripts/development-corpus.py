#!/usr/bin/env python3
"""Keep developer evaluation limited to the frozen 0.5.0 consultation set.

Use after a developer chooses demo cases for a quality check, before any audio
or transcript fixture is opened. The helper validates the ordered manifest,
rejects sealed or unexpected cases, and supplies only approved paths to the
scorer, downloader, or collision sweep that runs next.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST_PATH = (
    REPO_ROOT / "tests" / "fixtures" / "audio" / "development-corpus-0.5.0.json"
)
SCHEMA_VERSION = "ambient-scribe-development-corpus/v1"
EXPECTED_STEMS = (
    "primock57-day1-consultation02-i-have-sore-red-skin",
    "primock57-day1-consultation03-i-have-terrible-headache",
    "primock57-day1-consultation06-hard-to-breathe",
    "primock57-day1-consultation07-i-have-a-cough-and-cold",
    "primock57-day1-consultation08-i-have-dry-itchy-skin",
    "primock57-day2-consultation03-i-cant-hear-very-well-and-my-face-is-a-bit-numb",
    "primock57-day2-consultation09-i-cant-move-my-left-arm",
    "primock57-day3-consultation01-lips-swelling-after-eating-a-sandwich",
    "primock57-day5-consultation03-im-feeling-very-anxious",
    "primock57-day5-consultation09-tired-all-the-time",
)
SEALED_STEMS = frozenset(
    {
        "primock57-day2-consultation07-im-having-chest-discomfort",
        "primock57-day3-consultation03-i-dont-have-much-appetite-or-energy-lately",
        "primock57-day5-consultation04-lower-stomach-pain",
        "primock57-day1-consultation05-lower-abdominal-pain",
        "primock57-day2-consultation02-i-have-a-strange-swelling-on-my-elbow",
        "primock57-day5-consultation08-im-wheezy",
    }
)
FILE_SUFFIXES = {
    "wav": ".wav",
    "doctor_textgrid": ".doctor.TextGrid",
    "patient_textgrid": ".patient.TextGrid",
}
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class DevelopmentCorpusError(ValueError):
    """Explain why a developer-selected quality run was rejected.
    Use when the manifest or requested cases are unsafe, so evaluation stops clearly.
    The stable category lets later reports show the same failure without opening more data.
    """


@dataclass(frozen=True)
class DevelopmentFixture:
    """Carry one approved consultation and its exact evaluation files.
    Use after manifest validation when a developer runs a scorer or collision check.
    The explicit paths prevent the tool from discovering sealed local consultations.

    Attributes:
        ordinal: One-based position shown in evaluation output; zero or absent is invalid and stops the run.
        fixture_id: Stable consultation label shown to tools; empty is invalid and selects no fixture.
        stem: Filename identity for the approved consultation; empty is invalid and opens no files.
        wav_path: Approved replay audio; an absent path means the developer cannot start the fixture run.
        doctor_textgrid_path: Approved Doctor truth; an absent path means Doctor scoring cannot run.
        patient_textgrid_path: Approved Patient truth; an absent path means Patient scoring cannot run.
    """

    ordinal: int
    fixture_id: str
    stem: str
    wav_path: Path
    doctor_textgrid_path: Path
    patient_textgrid_path: Path


def _development_selection_error(category: str, detail: str) -> DevelopmentCorpusError:
    """Create the rejection shown for an unsafe developer request.
    Use before another fixture path is touched, so the quality run fails closed.

    Args:
        category: Stable failure class shown in evidence; empty would make the rejection unusable.
        detail: Safe developer-facing reason; empty means the category alone explains the stop.

    Returns:
        Error ready to raise; it is never null, so callers always stop with a reason.
    """
    return DevelopmentCorpusError(f"{category}: {detail}")


def _development_file_sha256(development_file_path: Path) -> str:
    """Hash one approved file for the developer's pre-run integrity check.
    Use only after manifest authorization; an empty file still returns its real digest.

    Args:
        development_file_path: Approved audio or truth file; an absent path raises instead of scoring.

    Returns:
        Lowercase SHA-256; an empty file returns its standard digest, never an empty value.
    """
    file_digest = hashlib.sha256()
    with development_file_path.open("rb") as development_file:
        # Read bounded pieces so a large demo recording does not stall the developer's machine.
        for file_chunk in iter(lambda: development_file.read(1024 * 1024), b""):
            file_digest.update(file_chunk)
    return file_digest.hexdigest()


def _manifest_fixture_records(
    manifest_document: dict[str, Any],
) -> list[dict[str, Any]]:
    """Read the ten consultation records selected for developer evaluation.
    Use before resolving paths; absent or empty records stop the run instead of scoring nothing.

    Args:
        manifest_document: Parsed manifest shown to evaluation tools; empty means no safe cases exist.

    Returns:
        Ten ordered records; an empty list is rejected and never reaches the developer's report.

    Raises:
        DevelopmentCorpusError: The fixture list is absent, incomplete, expanded, or malformed.
    """
    manifest_fixture_records = manifest_document.get("fixtures")

    # No fixture list means the developer cannot know which consultations are safe to run.
    if not isinstance(manifest_fixture_records, list):
        raise _development_selection_error("fixture_count", "fixtures must be a list")

    # The quality screen needs all ten frozen cases, not a silent partial or expanded set.
    if len(manifest_fixture_records) != len(EXPECTED_STEMS):
        raise _development_selection_error(
            "fixture_count",
            f"expected {len(EXPECTED_STEMS)}, got {len(manifest_fixture_records)}",
        )

    # Each visible consultation needs one structured record before its files can be verified.
    if not all(
        isinstance(manifest_fixture_record, dict)
        for manifest_fixture_record in manifest_fixture_records
    ):
        raise _development_selection_error(
            "fixture_count", "every fixture must be an object"
        )
    return manifest_fixture_records


def _validate_frozen_fixture_order(
    manifest_fixture_records: list[dict[str, Any]],
) -> None:
    """Confirm the developer sees the exact ten consultations in their frozen order.
    Use before file access so duplicates, sealed cases, and reordered menus fail safely.

    Args:
        manifest_fixture_records: Ten candidate records; empty means there is no safe evaluation set.

    Raises:
        DevelopmentCorpusError: An ID is duplicate, sealed, mismatched, or out of frozen order.
    """
    # Every consultation ID is read in display order for an exact menu comparison.
    fixture_ids = [
        str(manifest_fixture_record.get("fixture_id", ""))
        for manifest_fixture_record in manifest_fixture_records
    ]
    # Every file stem follows the same display order so an ID cannot point at another visit.
    fixture_stems = [
        str(manifest_fixture_record.get("stem", ""))
        for manifest_fixture_record in manifest_fixture_records
    ]

    # Any sealed case blocks the developer run before its local path is resolved.
    sealed_fixture_stems = [
        fixture_stem for fixture_stem in fixture_stems if fixture_stem in SEALED_STEMS
    ]
    # A sealed match means the quality run must stop without inspecting that consultation.
    if sealed_fixture_stems:
        raise _development_selection_error("sealed_stem", sealed_fixture_stems[0])

    # Duplicate IDs would make two menu entries appear to be the same consultation.
    if len(fixture_ids) != len(set(fixture_ids)):
        raise _development_selection_error(
            "duplicate_fixture_id", "fixture IDs must be unique"
        )

    # Duplicate stems could score one consultation twice and hide a missing case.
    if len(fixture_stems) != len(set(fixture_stems)):
        raise _development_selection_error(
            "duplicate_stem", "fixture stems must be unique"
        )

    # A different order would make later baseline rows incomparable for the developer.
    if tuple(fixture_stems) != EXPECTED_STEMS:
        raise _development_selection_error(
            "fixture_order", "manifest stems differ from frozen order"
        )

    # The visible ID must name the same consultation files it selects.
    if fixture_ids != fixture_stems:
        raise _development_selection_error(
            "fixture_order", "fixture_id must equal stem"
        )


def _approved_manifest_file_record(
    manifest_fixture: dict[str, Any],
    development_file_kind: str,
) -> tuple[str, dict[str, Any]]:
    """Read one consultation's declared audio or truth record.
    Use after fixture identity checks; absent metadata stops the developer run.

    Args:
        manifest_fixture: Approved consultation record; empty means no file can be selected.
        development_file_kind: `wav` or speaker truth kind; empty or unknown has no approved suffix.

    Returns:
        Fixture stem and file record; neither is empty after the frozen manifest is validated.

    Raises:
        DevelopmentCorpusError: The consultation has no structured file metadata or named record.
    """
    fixture_stem = str(manifest_fixture["stem"])
    manifest_files = manifest_fixture.get("files")

    # Missing file metadata leaves the developer without a safe artifact to score.
    if not isinstance(manifest_files, dict):
        raise _development_selection_error(
            "missing_file", f"{fixture_stem}: files must be an object"
        )
    manifest_file_record = manifest_files.get(development_file_kind)

    # Every selected consultation needs one exact record for this audio or truth file.
    if not isinstance(manifest_file_record, dict):
        raise _development_selection_error(
            "missing_file",
            f"{fixture_stem}: missing {development_file_kind} record",
        )
    return fixture_stem, manifest_file_record


def _approved_manifest_file_identity(
    fixture_stem: str,
    development_file_kind: str,
    manifest_file_record: dict[str, Any],
) -> tuple[Path, int, str]:
    """Validate the path, byte size, and hash declared for one approved file.
    Use before local file access so redirected or incomplete records fail closed.

    Args:
        fixture_stem: Approved consultation name; empty cannot identify a safe fixture.
        development_file_kind: Audio or speaker truth kind; empty has no approved suffix.
        manifest_file_record: Declared path and integrity fields; empty fails validation.

    Returns:
        Approved relative path, byte size, and digest; none are null or empty on success.

    Raises:
        DevelopmentCorpusError: The declared path, size, or hash is unsafe.
    """

    expected_relative_path = Path(
        f"tests/fixtures/audio/{fixture_stem}{FILE_SUFFIXES[development_file_kind]}"
    )
    manifest_relative_path = manifest_file_record.get("path")

    # A different path could redirect the developer to an unapproved local consultation.
    if manifest_relative_path != expected_relative_path.as_posix():
        raise _development_selection_error(
            "unexpected_fixture",
            f"{fixture_stem}: {development_file_kind} path must be {expected_relative_path}",
        )

    expected_file_bytes = manifest_file_record.get("bytes")
    expected_file_sha256 = manifest_file_record.get("sha256")

    # An absent or invalid byte count cannot prove the developer is scoring the frozen file.
    if not isinstance(expected_file_bytes, int) or expected_file_bytes < 0:
        raise _development_selection_error(
            "size_drift",
            f"{fixture_stem}: invalid {development_file_kind} byte size",
        )

    # An absent or malformed digest cannot protect the quality result from fixture drift.
    if (
        not isinstance(expected_file_sha256, str)
        or SHA256_PATTERN.fullmatch(expected_file_sha256) is None
    ):
        raise _development_selection_error(
            "hash_drift",
            f"{fixture_stem}: invalid {development_file_kind} SHA-256",
        )
    return expected_relative_path, expected_file_bytes, expected_file_sha256


def _verify_development_file_integrity(
    development_file_path: Path,
    expected_relative_path: Path,
    expected_file_bytes: int,
    expected_file_sha256: str,
) -> None:
    """Prove one approved local file still matches the frozen manifest.
    Use immediately before scoring; missing or changed files stop the quality run.

    Args:
        development_file_path: Resolved approved file; an absent file fails before scoring.
        expected_relative_path: Safe path shown in errors; empty would obscure the failed fixture.
        expected_file_bytes: Frozen size; zero is valid only for a deliberately empty approved file.
        expected_file_sha256: Frozen digest; empty or null was rejected before this check.

    Raises:
        DevelopmentCorpusError: The approved file is missing or its bytes no longer match.
    """
    # A missing approved file stops the run instead of showing an incomplete green report.
    if not development_file_path.is_file():
        raise _development_selection_error(
            "missing_file", expected_relative_path.as_posix()
        )

    # A byte-size change tells the developer that this is no longer the frozen artifact.
    if development_file_path.stat().st_size != expected_file_bytes:
        raise _development_selection_error(
            "size_drift", expected_relative_path.as_posix()
        )

    # A content change blocks comparison with the earlier baseline, even at the same size.
    if _development_file_sha256(development_file_path) != expected_file_sha256:
        raise _development_selection_error(
            "hash_drift", expected_relative_path.as_posix()
        )


def _validated_development_file_path(
    manifest_fixture: dict[str, Any],
    development_file_kind: str,
    workspace_root: Path,
    *,
    verify_development_files: bool,
) -> Path:
    """Resolve one approved audio or truth file for the next quality check.
    Use after identity validation; disabling byte checks returns the named path only.

    Args:
        manifest_fixture: Approved consultation record; empty means no file can be selected.
        development_file_kind: `wav` or speaker truth kind; empty or unknown has no approved suffix.
        workspace_root: Checkout containing development fixtures; absent means no local files resolve.
        verify_development_files: True opens and hashes the file; false returns the approved path only.

    Returns:
        Exact approved path; it is never null, and a missing file raises before scoring.

    Raises:
        DevelopmentCorpusError: Metadata, path, byte size, file presence, or hash is unsafe.
    """
    fixture_stem, manifest_file_record = _approved_manifest_file_record(
        manifest_fixture, development_file_kind
    )
    expected_relative_path, expected_file_bytes, expected_file_sha256 = (
        _approved_manifest_file_identity(
            fixture_stem, development_file_kind, manifest_file_record
        )
    )

    development_file_path = workspace_root / expected_relative_path

    # A manifest-only caller gets the approved path without opening the developer's fixture.
    if not verify_development_files:
        return development_file_path

    _verify_development_file_integrity(
        development_file_path,
        expected_relative_path,
        expected_file_bytes,
        expected_file_sha256,
    )
    return development_file_path


def load_development_fixtures(
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    workspace_root: Path = REPO_ROOT,
    *,
    verify_development_files: bool = True,
) -> tuple[DevelopmentFixture, ...]:
    """Load the exact consultations a developer may use for 0.5.0 evaluation.
    Use before a scorer or demo picker; an empty or invalid manifest fails instead of skipping.

    Args:
        manifest_path: Versioned consultation list; an absent file stops the run with an OS error.
        workspace_root: Checkout containing named fixtures; absent means no development files resolve.
        verify_development_files: True proves bytes and hashes; false supplies paths without opening them.

    Returns:
        Ten ordered approved fixtures; the tuple is never empty on success.

    Raises:
        DevelopmentCorpusError: Schema, identity, order, path, size, or hash violates the frozen contract.
        OSError: The manifest or an approved fixture cannot be read from the developer's checkout.
        json.JSONDecodeError: The manifest is not valid JSON, so no consultation is selected.
    """
    manifest_document = json.loads(manifest_path.read_text(encoding="utf-8"))

    # A non-object manifest cannot describe the safe cases shown to the developer.
    if not isinstance(manifest_document, dict):
        raise _development_selection_error(
            "schema_mismatch", "manifest must be an object"
        )

    # A different schema may change safety fields, so the developer must stop and update tooling.
    if manifest_document.get("schema_version") != SCHEMA_VERSION:
        raise _development_selection_error(
            "schema_mismatch", f"expected {SCHEMA_VERSION}"
        )

    manifest_fixture_records = _manifest_fixture_records(manifest_document)
    _validate_frozen_fixture_order(manifest_fixture_records)

    validated_development_fixtures: list[DevelopmentFixture] = []

    # Preserve the menu order so every baseline report compares the same consultation sequence.
    for expected_ordinal, manifest_fixture in enumerate(
        manifest_fixture_records, start=1
    ):
        # A mismatched ordinal would show or score the consultation in the wrong frozen position.
        if manifest_fixture.get("ordinal") != expected_ordinal:
            raise _development_selection_error(
                "fixture_order",
                f"{manifest_fixture['stem']}: expected ordinal {expected_ordinal}",
            )
        validated_development_fixtures.append(
            DevelopmentFixture(
                ordinal=expected_ordinal,
                fixture_id=str(manifest_fixture["fixture_id"]),
                stem=str(manifest_fixture["stem"]),
                wav_path=_validated_development_file_path(
                    manifest_fixture,
                    "wav",
                    workspace_root,
                    verify_development_files=verify_development_files,
                ),
                doctor_textgrid_path=_validated_development_file_path(
                    manifest_fixture,
                    "doctor_textgrid",
                    workspace_root,
                    verify_development_files=verify_development_files,
                ),
                patient_textgrid_path=_validated_development_file_path(
                    manifest_fixture,
                    "patient_textgrid",
                    workspace_root,
                    verify_development_files=verify_development_files,
                ),
            )
        )
    return tuple(validated_development_fixtures)


def validate_requested_stems(requested_fixture_stems: list[str]) -> None:
    """Check the consultations a developer explicitly selected for a quality run.
    Use before manifest access; an empty list means the full approved development set.

    Args:
        requested_fixture_stems: Ordered CLI choices; empty means use all ten approved consultations.

    Raises:
        DevelopmentCorpusError: A choice is sealed, unknown, duplicated, or out of frozen order.
    """
    # Validate each requested consultation before any manifest or fixture file is opened.
    for requested_fixture_stem in requested_fixture_stems:
        # A sealed choice is rejected without resolving or inspecting its local files.
        if requested_fixture_stem in SEALED_STEMS:
            raise _development_selection_error("sealed_stem", requested_fixture_stem)

        # An unknown choice cannot appear in the approved developer evaluation report.
        if requested_fixture_stem not in EXPECTED_STEMS:
            raise _development_selection_error(
                "unexpected_fixture", requested_fixture_stem
            )

    # Repeating a consultation could over-weight it and hide a missing development case.
    if len(requested_fixture_stems) != len(set(requested_fixture_stems)):
        raise _development_selection_error(
            "duplicate_stem", "requested stems must be unique"
        )

    # Convert each developer choice to its frozen position for an order-only comparison.
    manifest_positions = [
        EXPECTED_STEMS.index(requested_fixture_stem)
        for requested_fixture_stem in requested_fixture_stems
    ]

    # Out-of-order choices would make a later report incomparable with the frozen baseline.
    if manifest_positions != sorted(manifest_positions):
        raise _development_selection_error(
            "fixture_order", "requested stems differ from manifest order"
        )


def select_development_fixtures(
    development_fixtures: tuple[DevelopmentFixture, ...],
    requested_fixture_stems: list[str],
) -> tuple[DevelopmentFixture, ...]:
    """Return the approved consultations a developer asked to evaluate.
    Use after manifest validation; an empty choice returns all ten frozen cases.

    Args:
        development_fixtures: Validated consultation set; empty yields an empty result only for a bad caller.
        requested_fixture_stems: Ordered developer choices; empty selects the complete approved set.

    Returns:
        Chosen fixtures in frozen order; empty only when an invalid caller supplied no validated fixtures.
    """
    # No explicit choices means the developer requested the complete approved baseline set.
    if not requested_fixture_stems:
        return development_fixtures

    validate_requested_stems(requested_fixture_stems)

    # Index the approved consultations so each developer choice resolves without discovery.
    development_fixtures_by_stem = {
        development_fixture.stem: development_fixture
        for development_fixture in development_fixtures
    }
    # Return only the chosen cases in their already-validated baseline order.
    return tuple(
        development_fixtures_by_stem[requested_fixture_stem]
        for requested_fixture_stem in requested_fixture_stems
    )


def development_textgrid_paths(
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    workspace_root: Path = REPO_ROOT,
) -> tuple[Path, ...]:
    """Supply only approved Doctor and Patient truth files to a developer check.
    Use for vocabulary or scoring sweeps; an invalid manifest returns no paths and raises.

    Args:
        manifest_path: Versioned development list; an absent file stops the sweep before discovery.
        workspace_root: Checkout containing truth files; absent means no local paths can be supplied.

    Returns:
        Twenty ordered speaker-truth paths; the tuple is never empty on successful validation.
    """
    development_fixtures = load_development_fixtures(manifest_path, workspace_root)
    # Give the sweep both speaker truths for each visible development consultation.
    return tuple(
        development_textgrid_path
        for development_fixture in development_fixtures
        for development_textgrid_path in (
            development_fixture.doctor_textgrid_path,
            development_fixture.patient_textgrid_path,
        )
    )


def parse_development_corpus_arguments() -> argparse.Namespace:
    """Read the developer's manifest check or explicit consultation choices.
    Use at the CLI entry point; no `--stem` choices means validate all ten approved cases.

    Returns:
        Parsed CLI choices; the stem list is empty when the developer selected the full corpus.
    """
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    argument_parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    argument_parser.add_argument("--stem", action="append", default=[])
    argument_parser.add_argument("--json", action="store_true")
    return argument_parser.parse_args()


def main() -> int:
    """Show a developer the approved consultation set or one clear rejection.
    Use from the quality CLI before any scorer, downloader, or collision sweep runs.

    Returns:
        Exit 0 after listing approved cases, or 2 when the developer must fix an unsafe input.
    """
    # e.g. a developer chose ten demo consultations before starting a baseline report.
    command_arguments = parse_development_corpus_arguments()
    try:
        validate_requested_stems(command_arguments.stem)
        development_fixtures = load_development_fixtures(
            command_arguments.manifest, command_arguments.repo_root
        )
        selected_development_fixtures = select_development_fixtures(
            development_fixtures, command_arguments.stem
        )
    # e.g. a missing manifest or sealed demo choice becomes one safe CLI error, not a partial run.
    except (DevelopmentCorpusError, json.JSONDecodeError, OSError) as selection_error:
        print(f"development corpus rejected: {selection_error}", file=sys.stderr)
        return 2

    # Machine-readable output lets the next quality tool use the same cases without rediscovery.
    if command_arguments.json:
        print(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "fixture_count": len(selected_development_fixtures),
                    # Preserve the developer's approved order in the hand-off to the next tool.
                    "stems": [
                        development_fixture.stem
                        for development_fixture in selected_development_fixtures
                    ],
                    "status": "valid",
                },
                sort_keys=True,
            )
        )
    # Human-readable output lets a developer verify the consultation order before a long run.
    else:
        # Print one visible row per consultation in the exact order the baseline will use.
        for development_fixture in selected_development_fixtures:
            print(f"{development_fixture.ordinal:02d}\t{development_fixture.stem}")
        print(f"fixtures={len(selected_development_fixtures)} status=valid")
    return 0


# Running this file directly means a developer asked to validate the corpus before quality work.
if __name__ == "__main__":
    raise SystemExit(main())
