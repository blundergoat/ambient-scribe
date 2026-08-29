"""Terminal whole-visit source attestation for the clinician's note.

A note can be generated from a pre-terminal snapshot, and it looks completely normal when it happens. Consult 3.1 proved it:
the browser's 15-second wait gave up, correction captured 214 of the visit's 278 rows, and the generated plan silently
omitted the spoken emergency instructions.

This module owns the server-side truth that prevents that:

- one terminal watermark captured at finalization,
- canonical row-identity hashing, and
- a coverage check proving every meaningful live row survived into the corrected artifact.

A browser timeout may release the waiting UI, but only these attestations may authorize a note source. Hashes never leave
the server; the browser receives only an opaque attestation id and safe counts.
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
# A meaningful row counts as covered only when ONE corrected interval overlaps at least this share of it.
#
# Padding and merging were rejected: a padded union bridges the hole left by a short dropped row, and short rows are
# exactly the clinically dangerous drops, as consult 3.1's "we do" reversal showed.
COVERAGE_MIN_OVERLAP_RATIO = 0.25
# Rows this short ("mm-hm", "yes") accept any positive overlap instead of a ratio.
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

    Called when correction could not produce a reviewed transcript, to decide whether the clinician may still be offered a
    note built from the live rows instead.

    Args:
        reason_category: Safe failure label from the correction lane; empty or unknown categories still mean the pass failed.

    Returns:
        A bounded fallback reason, or None for an empty visit, which is the one category that can never justify a
        live-fallback note because there is no transcript to fall back to.
    """
    # An empty visit has nothing to fall back to. Every other failure is a correction-side problem sitting on top of a
    # complete live transcript, so the clinician can still be offered a note from those rows.
    if reason_category == "empty_audio":
        return None

    return FALLBACK_REASON_BY_CATEGORY.get(reason_category, "correction_error")


@dataclass(slots=True)
class TerminalWatermark:
    """One visit's server-owned terminal source identity.

    Captured after the final live rows are committed and role settlement has closed, immediately before the `finalized`
    event the clinician's browser is waiting on. Correction and summary requests are honest only when they bind to this
    identity, so a missing watermark means the visit is still finalizing and no note may be built yet.

    The terminal fields are frozen at capture; the correction lineage fields below them are filled in later, as the
    correction pass for this visit succeeds, fails coverage, or gives up.

    Attributes:
        session_id: Visit the clinician just stopped.
        attestation_id: Opaque id the browser may hold; the only identity field safe to send outside the server.
        finalization_epoch: Wall-clock time this watermark was captured.
        terminal_live_row_count: Number of live rows at finalization, including the finalize tail.
        terminal_live_row_hash: Canonical hash of those rows; correction must consume exactly this set.
        terminal_audio_seconds: Retained audio duration behind the terminal rows.
        terminal_trimmed_seconds: Audio the retention window dropped; zero for an ordinary visit.
        terminal_max_source_end: Latest end time across the terminal rows; zero for a visit with no rows.
        terminal_role_revision: Role revision closed at settlement.
        terminal_role_settlement: `settled`, or `failed_frozen` when the role worker did not drain in time.
        schema_version: Attestation shape version; a bump means the frozen identity form changed.
        correction_status: Empty until a correction attempt binds, then `attested_corrected`, `coverage_failed`,
            `unavailable:<reason>`, or `blocked`. Only `attested_corrected` authorizes a corrected note source.
        correction_input_row_hash: Rows the successful correction actually consumed; empty until one succeeds.
        corrected_output_row_hash: Canonical hash of the corrected rows; empty until one succeeds.
        coverage_version: Version of the coverage rule that produced the count below.
        unaccounted_meaningful_row_count: Meaningful live rows the corrected artifact failed to cover; zero on success.
        fallback_reason: Set only when a live-transcript note is permitted; empty means no fallback was authorized.
        extra: Open slot for future diagnostic fields; no current code path writes it.
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

    Row identity deliberately EXCLUDES the role label, because a clinician may correct Doctor to Patient after the visit
    ends and that must not invalidate the transcript's identity. Role lineage is tracked separately, through the
    settlement revision.

    Field order, float repr, NFC text, and null encoding are pinned by golden-vector tests; do not change them without a
    schema bump, or every previously attested visit stops matching.

    Args:
        rows: Stored rows in storage order; empty means an empty visit.

    Returns:
        One JSON array per row joined by newlines; empty string for no rows, which still hashes to a stable value.
    """
    lines: list[str] = []
    # Storage order is part of identity, so rows are serialized exactly as given rather than sorted.
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

    This is the value correction and summary requests are checked against, so a note can never be built from a different
    set of rows than the one that was attested.

    Args:
        rows: Stored rows in storage order; empty rows hash the empty string.

    Returns:
        Hex sha256 of the canonical serialization; never empty, even for a visit with no rows.
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

    This is the moment the clinician's Stop becomes authoritative: after it, correction and summary have one fixed set of
    rows to bind to, and anything arriving later cannot pass itself off as the whole visit.

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

    Checked at the top of every correction and summary request, so a browser that timed out early cannot talk its way into
    a note built from a partial visit.

    Args:
        session_id: Visit the browser is asking about.

    Returns:
        Watermark, or None meaning correction and summary must stay blocked. A null also covers a process restart, where
        the in-memory watermark is gone and the request safely fails closed.
    """
    return _terminal_watermarks.get(session_id)


def discard_terminal_watermark(session_id: str) -> None:
    """Invalidate the watermark when the visit resumes or is cleaned up.

    A reconnect inside the grace window means the clinician is still recording, so any earlier "terminal" identity is no
    longer true and must not be allowed to authorize a note for a visit that has since grown.

    Args:
        session_id: Visit whose watermark is no longer trustworthy.
    """
    _terminal_watermarks.pop(session_id, None)


def coverage_unaccounted_rows(
    input_rows: list[dict[str, Any]],
    corrected_rows: list[dict[str, Any]],
) -> list[str]:
    """List meaningful input rows the corrected artifact does not cover.

    Correction may resegment freely, so coverage is measured per interval: some one corrected row must overlap enough of
    each meaningful input row's span.

    A short, late, low-confidence, or marker-bearing row still counts as meaningful. The consult 3.1 "we do" reversal was
    exactly such a row, and losing it flipped the note's medication-availability state. Padded or merged unions are
    deliberately not used, because they bridge the very hole a dropped short row leaves between its neighbours.

    Args:
        input_rows: Terminal live rows the correction consumed.
        corrected_rows: Correction output rows; empty covers nothing, so every meaningful input row is returned.

    Returns:
        Segment ids of uncovered meaningful rows; empty means full coverage and the corrected note may be trusted.
    """
    intervals = [
        (float(row.get("start", 0.0)), float(row.get("end", 0.0)))
        for row in corrected_rows
    ]

    unaccounted: list[str] = []
    # Every terminal live row is checked, because the dangerous drop is always the one nobody thought to look for.
    for row in input_rows:
        # Rows without wording carry nothing a note could lose, so they cannot fail coverage.
        if str(row.get("text", "")).strip() == "":
            continue

        row_start = float(row.get("start", 0.0))
        row_end = float(row.get("end", 0.0))
        row_duration = max(0.0, row_end - row_start)
        best_overlap = max(
            (min(row_end, end) - max(row_start, start) for start, end in intervals),
            default=0.0,
        )

        # A tiny answer row such as "yes" or "we do" counts as covered on any real overlap, because a ratio would reject
        # rows that are clinically decisive but barely a fifth of a second long. Longer rows need a real share present.
        required_overlap = (
            0.0
            if row_duration < COVERAGE_MICRO_ROW_SECONDS
            else COVERAGE_MIN_OVERLAP_RATIO * row_duration
        )
        # Too little overlap means this row's wording is missing from the corrected artifact, so the note must not claim completeness.
        if best_overlap <= required_overlap:
            unaccounted.append(str(row.get("segment_id", "")))

    return unaccounted
