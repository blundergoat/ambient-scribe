"""
Summary request helpers for browser-visible transcript text.

The browser asks for a summary of the transcript rows the user can see. These
helpers normalise that browser snapshot, update session storage, and publish
summary outputs while keeping `server.py` focused on HTTP routing and
status codes.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field
from storage import StorageBackend

logger = logging.getLogger(__name__)

PublishToMercure = Callable[[str, dict[str, Any], int | None], Awaitable[bool]]


class BrowserVisibleSegment(BaseModel):
    """
    Transcript segment sent back from the browser-visible transcript view.

    Use when the server needs the same transcript subset the user can see
    before generating a summary.
    Empty text means the row is ignored because it would not help the note.
    An empty `segment_id` means an older page without row identity; those rows
    cannot be merged into a populated stored history and are skipped there.
    """

    speaker_id: str = "UNKNOWN"
    text: str = ""
    start: float = 0.0
    end: float = 0.0
    role: str | None = None
    segment_id: str = ""
    # Row ASR confidence echoed back by the browser; None means the row never
    # carried one and a restore-from-browser keeps it unmeasured.
    confidence: float | None = None


class SummaryRequest(BaseModel):
    """
    Optional summary body containing browser-visible transcript text.

    Use when the user asks for a note from only the transcript currently on
    screen. Empty segments mean the route should use the stored session
    transcript instead.
    """

    segments: list[BrowserVisibleSegment] = Field(default_factory=list)


@dataclass(slots=True)
class SummaryContext:
    """
    Transcript context selected for one summary request.

    Use in the FastAPI summary route after the clinician clicks Summarise.
    Corrected sources mean a slower post-visit pass has produced higher-quality
    rows. Browser-visible sources mean the UI sent the rows on screen;
    session-store sources mean the user summarized the full stored transcript.

    Attributes:
        stored_segments: Rows selected for summary; empty means the user has no text.
        transcript: Role-attributed text sent to the summary agent; empty cannot summarize.
        source: Log label explaining whether UI replay rows or stored rows were used.
        citation_segments: Rows whose `segment_id` values may be cited by the
            summary agent; empty keeps the legacy uncited summary flow.
    """

    stored_segments: list[dict[str, Any]]
    transcript: str
    source: str
    citation_segments: list[dict[str, Any]]


def build_summary_context(
    session_id: str,
    summary_request: SummaryRequest | None,
    sessions: StorageBackend,
) -> SummaryContext:
    """Choose the transcript text used by the summary agent.

    Args:
        session_id: Browser session UUID; empty would not map to stored rows.
        summary_request: Optional browser-visible replay rows; null uses storage.
        sessions: Transcript store; empty store means there is nothing to summarize.

    Returns:
        SummaryContext with selected rows, text, and source label for logs.
    """
    corrected_segments = sessions.get_corrected_segments(session_id)
    # Corrected rows are opt-in at the storage layer, but summary generation is
    # the first consumer that should prefer them once the slower pass exists.
    if corrected_segments:
        return SummaryContext(
            stored_segments=corrected_segments,
            transcript=sessions.get_corrected_transcript_text(
                session_id, max_chars=8000
            ),
            source="corrected_segments",
            citation_segments=corrected_segments,
        )

    browser_visible_segments = browser_visible_segments_from_summary(summary_request)
    # Browser-provided rows anchor the note to the transcript the user can see,
    # merged so rows the browser missed (finalize flush, dropped events) still
    # reach the note instead of being deleted from server history.
    if browser_visible_segments:
        merge_result = sessions.merge_browser_segments(
            session_id, browser_visible_segments
        )
        # Unknown rows or a restore-from-browser are worth a trace when
        # diagnosing a summary that does not match the stored transcript.
        if merge_result.get("unknown", 0) or merge_result.get("restored"):
            logger.info(
                "summary.segments_merged session_id=%s matched=%s unknown=%s restored=%s",
                session_id,
                merge_result.get("matched", 0),
                merge_result.get("unknown", 0),
                merge_result.get("restored", False),
                extra={"session_id": session_id, **merge_result},
            )
        # Reading the rows back applies server-side row corrections and keeps
        # tail rows the browser never received, so the note covers the whole
        # stored consultation with the labels the clinician trusts.
        merged_rows = sessions.get_segments(session_id)
        return SummaryContext(
            stored_segments=merged_rows,
            transcript=transcript_text_from_segments(merged_rows),
            source="browser_visible_segments",
            citation_segments=[],
        )

    return SummaryContext(
        stored_segments=sessions.get_segments(session_id),
        transcript=sessions.get_transcript_text(session_id, max_chars=8000),
        source="session_store",
        citation_segments=[],
    )


def browser_visible_segments_from_summary(
    summary_request: SummaryRequest | None,
) -> list[dict[str, Any]]:
    """Return summary request segments that contain visible transcript text.

    Args:
        summary_request: Optional JSON body from the browser; null means use stored text.

    Returns:
        Segment dicts with non-empty text; empty means the route should use storage.
    """
    # Empty request body means live recordings should keep using stored session text.
    if summary_request is None:
        return []

    return normalise_browser_visible_segments(summary_request.segments)


def normalise_browser_visible_segments(
    browser_segments: list[BrowserVisibleSegment],
) -> list[dict[str, Any]]:
    """Convert browser-visible segment models into storage-safe dictionaries.

    Args:
        browser_segments: Browser transcript rows; empty means no text is visible.

    Returns:
        Segment dictionaries; rows with blank text are skipped for summary quality.
    """
    normalised_segments: list[dict[str, Any]] = []
    # Every browser-visible row becomes one role-aware summary line.
    for browser_segment in browser_segments:
        segment_payload = browser_segment.model_dump()
        text = str(segment_payload.get("text", "")).strip()
        # Blank text is not useful to the clinician's note.
        if text == "":
            continue

        segment_payload["text"] = text
        # Unmeasured rows drop the key so stored history stays absent-is-absent.
        if segment_payload.get("confidence") is None:
            segment_payload.pop("confidence", None)
        normalised_segments.append(segment_payload)

    return normalised_segments


def transcript_text_from_segments(
    transcript_segments: list[dict[str, Any]],
    max_chars: int = 8000,
) -> str:
    """Build role-attributed transcript text from browser-visible segments.

    Args:
        transcript_segments: Visible rows selected by the user flow; empty returns no text.
        max_chars: Summary context budget; zero returns an empty transcript.

    Returns:
        Plain transcript text used by the summary agent; empty means nothing can be summarized.
    """
    # A zero budget lets tests or callers intentionally request no summary context.
    if max_chars <= 0:
        return ""

    transcript_lines: list[str] = []
    # Each visible segment keeps the role label the clinician saw in the browser.
    for segment in transcript_segments:
        speaker = segment.get("role") or segment.get("speaker_id") or "UNKNOWN"
        text = str(segment.get("text", "")).strip()
        # Empty text rows were already filtered, but this keeps the helper safe.
        if text == "":
            continue

        transcript_lines.append(f"[{speaker}] {text}")

    full_transcript = "\n".join(transcript_lines)
    return full_transcript[:max_chars]


async def publish_summary_outputs(
    session_id: str,
    summary: dict[str, Any],
    *,
    mercure_event_ids: dict[str, int],
    publish_to_mercure: PublishToMercure,
    logger: logging.Logger,
) -> None:
    """Publish summary output for the browser.

    Args:
        session_id: Browser session UUID; empty would publish to the wrong topic.
        summary: Summary payload; empty still publishes an empty panel state.
        mercure_event_ids: Per-session counters used for EventSource resume.
        publish_to_mercure: Browser event publisher; false means HTTP still returns data.
        logger: Process logger; null is not accepted because failures should be visible.
    """
    mercure_event_ids.setdefault(session_id, 0)
    mercure_event_ids[session_id] += 1
    delivered = await publish_to_mercure(
        f"scribe/session/{session_id}/summary",
        {
            "type": "summary",
            "session_id": session_id,
            **summary,
        },
        event_id=mercure_event_ids[session_id],
    )
    if not delivered:
        logger.warning(
            "summary.publish_failed session_id=%s",
            session_id,
            extra={"session_id": session_id},
        )
