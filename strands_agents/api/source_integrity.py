"""Terminal whole-visit source attestation for the clinician's note.

Consult 3.1 proved a note can be generated from a pre-terminal snapshot: the
browser's 15-second wait gave up, correction captured 214 of the visit's 278
rows, and the generated plan silently omitted the spoken emergency
instructions. This module owns the server-side truth that prevents that: one
terminal watermark captured at finalization, canonical row-identity hashing,
and a coverage check proving every meaningful live row survived into the
corrected artifact. Browser timeouts may release waiting UI; only these
attestations may authorize a note source. Hashes never leave the server -
the browser receives only an opaque attestation id and safe counts.
"""

from __future__ import annotations

import hashlib
import json
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from typing import Any

SOURCE_INTEGRITY_SCHEMA_VERSION = 1
COVERAGE_VERSION = 1
# A meaningful row counts as covered only when ONE corrected interval overlaps
# at least this share of it. Padding/merging was rejected: a padded union
# bridges the hole left by a short dropped row, and short rows are exactly the
# clinically dangerous drops (consult 3.1's "we do" reversal).
COVERAGE_MIN_OVERLAP_RATIO = 0.25
# Rows this short (mm-hm, "yes") accept any positive overlap instead of a ratio.
COVERAGE_MICRO_ROW_SECONDS = 0.2

# Fallback reasons that may use the attested complete live transcript.
LIVE_FALLBACK_REASONS = frozenset(
    {
        "correction_error",
        "correction_timeout",
        "retained_audio_unavailable",
        "retention_window",
    }
)
# Existing correction reason categories mapped onto the bounded fallback enum.
FALLBACK_REASON_BY_CATEGORY = {
    "audio_expired": "retained_audio_unavailable",
    "retention_window": "retention_window",
    "transcribe_failed": "correction_error",
    "device_not_ready": "correction_error",
    "gpu_transient": "correction_error",
    "gpu_fatal": "correction_error",
    "empty_transcription": "correction_error",
    "request_failed": "correction_error",
    "timeout": "correction_timeout",
}


def fallback_reason_for_category(reason_category: str) -> str | None:
    """Map a correction failure category onto the bounded fallback enum.

    Args:
        reason_category: Safe failure label from the correction lane; empty
            or unknown categories still mean the correction pass failed.

    Returns:
        A bounded fallback reason, or None for an empty visit - the one
        category that can never justify a live-fallback note.
    """
    # An empty visit has nothing to fall back to; every other failure is a
    # correction-side problem over a complete live transcript.
    if reason_category == "empty_audio":
        return None

    return FALLBACK_REASON_BY_CATEGORY.get(reason_category, "correction_error")


@dataclass(slots=True)
class TerminalWatermark:
    """One visit's server-owned terminal source identity.

    Captured after the final live rows are committed and role settlement has
    closed, immediately before the `finalized` event the user's browser waits
    for. Correction and summary requests are honest only when they bind to
    this identity; a missing watermark means the visit is still finalizing.
    """

    session_id: str
    attestation_id: str
    finalization_epoch: float
    terminal_live_row_count: int
    terminal_live_row_hash: str
    terminal_audio_seconds: float
    terminal_trimmed_seconds: float
    terminal_max_source_end: float
    terminal_role_revision: int
    terminal_role_settlement: str
    schema_version: int = SOURCE_INTEGRITY_SCHEMA_VERSION
    # Correction lineage; empty means no correction attempt has bound yet.
    correction_status: str = ""
    correction_input_row_hash: str = ""
    corrected_output_row_hash: str = ""
    coverage_version: int = COVERAGE_VERSION
    unaccounted_meaningful_row_count: int = 0
    fallback_reason: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


# One watermark per session, in memory only: a process restart safely fails
# closed - late correction/summary requests see "source_not_terminal".
_terminal_watermarks: dict[str, TerminalWatermark] = {}


def canonical_row_lines(rows: list[dict[str, Any]]) -> str:
    """Serialize transcript rows into the frozen identity form.

    Row identity deliberately EXCLUDES the role label: clinicians may correct
    speaker roles after the visit ends without invalidating transcript
    identity - role lineage is tracked separately through the settlement
    revision. Field order, float repr, NFC text, and null encoding are pinned
    by golden-vector tests; do not change them without a schema bump.

    Args:
        rows: Stored rows in storage order; empty means an empty visit.

    Returns:
        One JSON array per row joined by newlines; empty string for no rows.
    """
    lines: list[str] = []
    # Storage order is part of identity, so rows are serialized as given.
    for row in rows:
        confidence = row.get("confidence")
        lines.append(
            json.dumps(
                [
                    str(row.get("segment_id", "")),
                    str(row.get("speaker_id", "")),
                    float(row.get("start", 0.0)),
                    float(row.get("end", 0.0)),
                    unicodedata.normalize("NFC", str(row.get("text", ""))),
                    float(confidence) if confidence is not None else None,
                    bool(row.get("is_interim", False)),
                    int(row.get("revision", 0) or 0),
                ],
                separators=(",", ":"),
                ensure_ascii=False,
            )
        )

    return "\n".join(lines)


def canonical_rows_hash(rows: list[dict[str, Any]]) -> str:
    """Digest row identity so producers and verifiers can compare visits.

    Args:
        rows: Stored rows in storage order; empty rows hash the empty string.

    Returns:
        Hex sha256 of the canonical serialization.
    """
    return hashlib.sha256(canonical_row_lines(rows).encode("utf-8")).hexdigest()


def record_terminal_watermark(
    session_id: str,
    rows: list[dict[str, Any]],
    *,
    audio_seconds: float,
    trimmed_seconds: float,
    role_revision: int,
    role_settlement: str,
) -> TerminalWatermark:
    """Freeze the visit's terminal identity right before `finalized` publishes.

    Args:
        session_id: Visit the clinician just stopped.
        rows: Complete stored live rows including the finalize tail; empty
            means the user stopped before any usable speech.
        audio_seconds: Retained audio duration for the watermark.
        trimmed_seconds: Audio dropped by the retention window; zero for
            ordinary visits.
        role_revision: Closed role revision at settlement.
        role_settlement: `settled`, or `failed_frozen` when the role worker
            did not drain inside the bound (the note stays review-required).

    Returns:
        The stored watermark; later requests bind to its identity.
    """
    watermark = TerminalWatermark(
        session_id=session_id,
        attestation_id=uuid.uuid4().hex,
        finalization_epoch=time.time(),
        terminal_live_row_count=len(rows),
        terminal_live_row_hash=canonical_rows_hash(rows),
        terminal_audio_seconds=audio_seconds,
        terminal_trimmed_seconds=trimmed_seconds,
        terminal_max_source_end=max(
            (float(row.get("end", 0.0)) for row in rows), default=0.0
        ),
        terminal_role_revision=role_revision,
        terminal_role_settlement=role_settlement,
    )
    _terminal_watermarks[session_id] = watermark
    return watermark


def get_terminal_watermark(session_id: str) -> TerminalWatermark | None:
    """Return the visit's watermark, or None while it is still finalizing.

    Args:
        session_id: Visit the browser is asking about.

    Returns:
        Watermark, or None meaning correction/summary must stay blocked.
    """
    return _terminal_watermarks.get(session_id)


def discard_terminal_watermark(session_id: str) -> None:
    """Invalidate the watermark when the visit resumes or is cleaned up.

    A reconnect inside the grace window continues recording, so any earlier
    "terminal" identity is no longer true and must not authorize a note.

    Args:
        session_id: Visit whose watermark is no longer trustworthy.
    """
    _terminal_watermarks.pop(session_id, None)


def coverage_unaccounted_rows(
    input_rows: list[dict[str, Any]],
    corrected_rows: list[dict[str, Any]],
) -> list[str]:
    """List meaningful input rows the corrected artifact does not cover.

    Correction may resegment, so coverage is temporal per interval: some ONE
    corrected row must overlap enough of each meaningful input row's span.
    A short, late, low-confidence, or marker-bearing row is still meaningful -
    the consult 3.1 "we do" reversal is exactly such a row, and losing it
    flipped the note's medication-availability state. Padded/merged unions are
    deliberately not used because they bridge the hole a dropped short row
    leaves between its neighbors.

    Args:
        input_rows: Terminal live rows the correction consumed.
        corrected_rows: Correction output rows; empty covers nothing.

    Returns:
        Segment ids of uncovered meaningful rows; empty means full coverage.
    """
    intervals = [
        (float(row.get("start", 0.0)), float(row.get("end", 0.0)))
        for row in corrected_rows
    ]

    unaccounted: list[str] = []
    for row in input_rows:
        # Rows without wording carry nothing a note could lose.
        if str(row.get("text", "")).strip() == "":
            continue

        row_start = float(row.get("start", 0.0))
        row_end = float(row.get("end", 0.0))
        row_duration = max(0.0, row_end - row_start)
        best_overlap = max(
            (min(row_end, end) - max(row_start, start) for start, end in intervals),
            default=0.0,
        )

        # A tiny answer row ("yes", "we do") counts as covered on any real
        # overlap; longer rows need a meaningful share of their span present.
        required_overlap = (
            0.0
            if row_duration < COVERAGE_MICRO_ROW_SECONDS
            else COVERAGE_MIN_OVERLAP_RATIO * row_duration
        )
        if best_overlap <= required_overlap:
            unaccounted.append(str(row.get("segment_id", "")))

    return unaccounted
