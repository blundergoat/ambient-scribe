"""Protect the development Demo Audio picker during fixture refreshes.

Use these pure catalog rules before PriMock downloads or manifest replacement.
They bind remote case IDs to the tracked ten-case development manifest, merge a
selected refresh without dropping rows, and publish the complete catalog atomically.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

DEVELOPMENT_CORPUS_MANIFEST_PATH = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "audio"
    / "development-corpus-0.5.0.json"
)
PRIMOCK57_DEVELOPMENT_STEM = re.compile(r"^primock57-(day\d+-consultation\d+)(?:-|$)")
PRIMOCK57_DEVELOPMENT_CASE_IDS = frozenset(
    (
        "day1_consultation02 day1_consultation03 day1_consultation06 "
        "day1_consultation07 day1_consultation08 day2_consultation03 "
        "day2_consultation09 day3_consultation01 day5_consultation03 "
        "day5_consultation09"
    ).split()
)


def legacy_primock57_filename(case_id: str) -> str:
    """Return the old case-only PriMock filename accepted by `--case`.

    Args:
        case_id: PriMock case ID; empty would create an unusable legacy name.

    Returns:
        Back-compatible WAV filename; it is never empty for a non-empty case ID.
    """
    return f"primock57-{case_id.replace('_', '-')}.wav"


def slug_for_filename(value: str) -> str:
    """Convert user-visible complaint text into a safe filename fragment.

    Args:
        value: Complaint text; empty or punctuation-only values produce an empty slug.

    Returns:
        Lowercase filename slug; empty means callers should use a fallback.
    """
    normalized_value = value.lower().replace("'", "")
    return re.sub(r"[^a-z0-9]+", "-", normalized_value).strip("-")


def primock57_filename(case_id: str, complaint: str) -> str:
    """Build a self-documenting local WAV filename for one PriMock case.

    Args:
        case_id: PriMock case ID used to keep filenames unique in the picker.
        complaint: Presenting complaint; empty falls back to the case-only name.

    Returns:
        WAV filename accepted by legacy `--case` filters.
    """
    complaint_slug = slug_for_filename(complaint)
    # Empty complaint text keeps the old case-only filename usable.
    if not complaint_slug:
        return legacy_primock57_filename(case_id)
    return f"{legacy_primock57_filename(case_id).removesuffix('.wav')}-{complaint_slug}.wav"


def primock57_case_label(case_id: str) -> str:
    """Convert a PriMock case ID into the label shown in the picker.

    Args:
        case_id: PriMock case ID; empty or unknown shapes fall back to spaced text.

    Returns:
        Human-readable day and consultation label; never null.
    """
    case_match = re.fullmatch(r"day(\d+)_consultation(\d+)", case_id)
    # Unknown case-ID shapes still need readable text in the picker.
    if not case_match:
        return case_id.replace("_", " ")
    day, consultation = case_match.groups()
    return f"day {int(day)} consultation {int(consultation)}"


def load_development_primock57_catalog(
    manifest_path: Path = DEVELOPMENT_CORPUS_MANIFEST_PATH,
) -> tuple[tuple[str, str], ...]:
    """Read ordered PriMock case IDs and picker stems allowed for development.

    Args:
        manifest_path: Tracked corpus contract; absent or malformed authorizes no download.

    Returns:
        Ordered `(case_id, stem)` rows; empty manifests are rejected before remote access.

    Raises:
        RuntimeError: The manifest is missing, malformed, duplicated, or outside the frozen set.
    """
    try:
        manifest_document = json.loads(manifest_path.read_text(encoding="utf-8"))
    # An unreadable contract cannot safely authorize local picker downloads.
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"development manifest unavailable: {error}") from error
    # A different schema may redefine the corpus boundary, so generation stops.
    if (
        not isinstance(manifest_document, dict)
        or manifest_document.get("schema_version")
        != "ambient-scribe-development-corpus/v1"
    ):
        raise RuntimeError("development manifest has an unsupported schema")
    manifest_fixtures = manifest_document.get("fixtures")
    # The picker contract is exactly ten structured development consultations.
    if not isinstance(manifest_fixtures, list) or len(manifest_fixtures) != 10:
        raise RuntimeError("development manifest must contain ten fixtures")

    approved_catalog: list[tuple[str, str]] = []
    # Each manifest stem supplies the remote case identity and final picker filename.
    for manifest_fixture in manifest_fixtures:
        # A malformed row cannot authorize a remote note or audio request.
        if not isinstance(manifest_fixture, dict):
            raise RuntimeError("development manifest fixture must be an object")
        fixture_stem = manifest_fixture.get("stem")
        # Missing or unexpected stems cannot be mapped to PriMock source channels.
        if not isinstance(fixture_stem, str):
            raise RuntimeError("development manifest fixture stem must be text")
        case_match = PRIMOCK57_DEVELOPMENT_STEM.match(fixture_stem)
        # Non-PriMock rows are outside this downloader's approved source boundary.
        if case_match is None:
            raise RuntimeError(
                f"development manifest has unsupported stem: {fixture_stem}"
            )
        case_id = case_match.group(1).replace("-", "_")
        approved_catalog.append((case_id, fixture_stem))

    approved_case_ids = tuple(case_id for case_id, _stem in approved_catalog)
    approved_stems = tuple(stem for _case_id, stem in approved_catalog)
    # Duplicates could over-weight one consultation while hiding another.
    if len(set(approved_case_ids)) != 10 or len(set(approved_stems)) != 10:
        raise RuntimeError("development manifest contains duplicate PriMock fixtures")
    # The local defense-in-depth set must move with the tracked source-of-truth manifest.
    if frozenset(approved_case_ids) != PRIMOCK57_DEVELOPMENT_CASE_IDS:
        raise RuntimeError("development manifest differs from the PriMock allowlist")
    return tuple(approved_catalog)


def is_playable_primock57_case(
    case_id: str,
    files: dict[str, str],
    approved_case_ids: frozenset[str] | None = None,
) -> bool:
    """Return whether a PriMock case can appear in the development picker.

    Args:
        case_id: PriMock case ID; non-development and sealed IDs stay hidden.
        files: Role filename map; missing channels cannot be mixed into replay audio.
        approved_case_ids: Manifest-derived allowlist; null loads the tracked contract.

    Returns:
        True only when both channels exist and the manifest authorizes the case.
    """
    # Direct callers still use the tracked manifest rather than a drifting local constant.
    if approved_case_ids is None:
        approved_case_ids = frozenset(
            approved_case_id
            for approved_case_id, _stem in load_development_primock57_catalog()
        )
    # Only manifest-approved visits may appear in the Demo Audio picker.
    if case_id not in approved_case_ids:
        return False
    return "doctor" in files and "patient" in files


def _picker_entry_identity(manifest_entry: dict[str, object]) -> str:
    """Return the stable case or filename identity used during one refresh.

    Args:
        manifest_entry: Existing or refreshed picker row; empty has no safe identity.

    Returns:
        PriMock case ID when present, otherwise the synthetic filename.

    Raises:
        RuntimeError: The row has no non-empty string identity.
    """
    case_id = manifest_entry.get("case_id")
    # PriMock complaint wording may change, but its case ID owns the same picker slot.
    if isinstance(case_id, str) and case_id:
        return f"primock57:{case_id}"
    filename = manifest_entry.get("filename")
    # Synthetic rows use their stable user-visible WAV name as identity.
    if isinstance(filename, str) and filename:
        return f"filename:{filename}"
    raise RuntimeError("picker manifest entry has no case_id or filename")


def validate_complete_primock57_catalog(
    manifest_entries: list[dict[str, object]],
) -> None:
    """Require all ten approved PriMock rows in development display order.

    Args:
        manifest_entries: Candidate picker rows; empty is an incomplete catalog.

    Raises:
        RuntimeError: Rows are partial, reordered, duplicated, or point at wrong WAV names.
    """
    approved_catalog = load_development_primock57_catalog()
    expected_case_ids = tuple(case_id for case_id, _stem in approved_catalog)
    expected_filenames = tuple(f"{stem}.wav" for _case_id, stem in approved_catalog)
    actual_case_ids = tuple(str(entry.get("case_id", "")) for entry in manifest_entries)
    actual_filenames = tuple(
        str(entry.get("filename", "")) for entry in manifest_entries
    )
    # A partial or reordered catalog would hide approved demos from the user.
    if actual_case_ids != expected_case_ids or actual_filenames != expected_filenames:
        raise RuntimeError(
            "partial refresh requires the complete development catalog in manifest order"
        )


def merge_picker_catalog(
    existing_entries: list[dict[str, object]],
    refreshed_entries: list[dict[str, object]],
    *,
    require_complete_primock57: bool,
) -> list[dict[str, object]]:
    """Replace selected picker rows without dropping unselected consultations.

    Args:
        existing_entries: Published catalog; empty cannot support a partial refresh.
        refreshed_entries: Newly generated rows; empty leaves the catalog unchanged.
        require_complete_primock57: True freezes the ten-case development order.

    Returns:
        Complete catalog in its prior display order with selected rows replaced.

    Raises:
        RuntimeError: Existing rows are incomplete, duplicated, or missing a refreshed identity.
    """
    # Development refreshes start only from the complete tracked ten-row picker.
    if require_complete_primock57:
        validate_complete_primock57_catalog(existing_entries)
    merged_entries = list(existing_entries)
    entry_positions: dict[str, int] = {}
    # Each existing row owns one immutable display position.
    for entry_position, manifest_entry in enumerate(merged_entries):
        entry_identity = _picker_entry_identity(manifest_entry)
        # Duplicate identities make replacement ambiguous and must not be published.
        if entry_identity in entry_positions:
            raise RuntimeError(
                f"picker manifest has duplicate identity: {entry_identity}"
            )
        entry_positions[entry_identity] = entry_position
    # A selected row replaces only its existing catalog slot.
    for refreshed_entry in refreshed_entries:
        refreshed_identity = _picker_entry_identity(refreshed_entry)
        # A partial refresh cannot silently add an unreviewed picker row.
        if refreshed_identity not in entry_positions:
            raise RuntimeError(
                f"partial refresh identity is absent from picker catalog: {refreshed_identity}"
            )
        merged_entries[entry_positions[refreshed_identity]] = refreshed_entry
    # Replacements must retain all ten exact PriMock identities and filenames.
    if require_complete_primock57:
        validate_complete_primock57_catalog(merged_entries)
    return merged_entries


def load_picker_catalog(manifest_path: Path) -> list[dict[str, object]]:
    """Load an existing picker catalog before selected rows are refreshed.

    Args:
        manifest_path: Published JSON list; missing means no partial base exists.

    Returns:
        Structured rows in current picker order; empty JSON lists are allowed for validation.

    Raises:
        RuntimeError: Existing bytes are unreadable or not a list of objects.
    """
    try:
        manifest_document = json.loads(manifest_path.read_text(encoding="utf-8"))
    # A malformed catalog must remain untouched rather than being replaced by a partial run.
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"picker manifest unavailable: {error}") from error
    # Only structured picker rows can be merged without losing user-visible identity.
    if not isinstance(manifest_document, list) or any(
        not isinstance(entry, dict) for entry in manifest_document
    ):
        raise RuntimeError("picker manifest must be a list of objects")
    return manifest_document


def write_picker_catalog(
    manifest_path: Path, manifest_entries: list[dict[str, object]]
) -> None:
    """Atomically replace the picker catalog after every row is complete.

    Args:
        manifest_path: Published catalog selected by the browser; parent must be writable.
        manifest_entries: Complete ordered rows; empty intentionally publishes an empty list.
    """
    manifest_bytes = (json.dumps(manifest_entries, indent=2) + "\n").encode("utf-8")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    staged_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{manifest_path.name}.",
            suffix=".tmp",
            dir=manifest_path.parent,
            delete=False,
        ) as staged_manifest:
            staged_path = Path(staged_manifest.name)
            staged_manifest.write(manifest_bytes)
            staged_manifest.flush()
            os.fsync(staged_manifest.fileno())
        os.replace(staged_path, manifest_path)
    finally:
        # A failed flush or replace leaves the prior picker catalog untouched.
        if staged_path is not None:
            staged_path.unlink(missing_ok=True)
