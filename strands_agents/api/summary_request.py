"""
Summary and replay-stop request helpers for browser-visible transcript text.

The browser may stop demo audio before the WAV ends, then ask for a summary of
only the transcript rows it actually revealed. These helpers normalise that
browser snapshot, update session storage, and publish summary/hint outputs while
keeping `server.py` focused on HTTP routing and status codes.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field
from storage import StorageBackend

PublishToMercure = Callable[[str, dict[str, Any], int | None], Awaitable[bool]]
GenerateClinicalHints = Callable[[str], list[dict[str, str]]]


class BrowserVisibleSegment(BaseModel):
    """
    Transcript segment sent back from the browser-visible replay view.

    Use when the user stops demo audio early and the server needs the same
    transcript subset the user can see before generating a summary.
    Empty text means the row is ignored because it would not help the note.
    """

    speaker_id: str = "UNKNOWN"
    text: str = ""
    start: float = 0.0
    end: float = 0.0
    role: str | None = None


class SummaryRequest(BaseModel):
    """
    Optional summary body containing browser-visible transcript text.

    Use when the browser has replayed or stopped a WAV and the user asks for a
    note from only the transcript currently on screen. Empty segments mean the
    route should use the stored live-session transcript instead.
    """

    segments: list[BrowserVisibleSegment] = Field(default_factory=list)


class ReplayStopRequest(BaseModel):
    """
    Optional replay-stop body containing the transcript revealed so far.

    Use when the user stops or finishes demo audio and the backend needs the
    same transcript snapshot before summary generation. Empty segments mean
    the route only cancels any legacy replay task.
    """

    visible_segments: list[BrowserVisibleSegment] = Field(default_factory=list)
    audio_time_seconds: float | None = None
    was_completed: bool = False


@dataclass(slots=True)
class SummaryContext:
    """
    Transcript context selected for one summary request.

    Use in the FastAPI summary route after the clinician clicks Summarise.
    Browser-visible sources mean replay stopped early; session-store sources
    mean the user summarized the full live/stored transcript.

    Attributes:
        stored_segments: Rows selected for summary; empty means the user has no text.
        transcript: Role-attributed text sent to the summary agent; empty cannot summarize.
        source: Log label explaining whether UI replay rows or stored rows were used.
    """

    stored_segments: list[dict[str, Any]]
    transcript: str
    source: str


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
    browser_visible_segments = browser_visible_segments_from_summary(summary_request)
    # Replay summaries use exactly the transcript the browser has revealed.
    if browser_visible_segments:
        sessions.replace_segments(session_id, browser_visible_segments)
        return SummaryContext(
            stored_segments=browser_visible_segments,
            transcript=transcript_text_from_segments(browser_visible_segments),
            source="browser_visible_segments",
        )

    return SummaryContext(
        stored_segments=sessions.get_segments(session_id),
        transcript=sessions.get_transcript_text(session_id, max_chars=8000),
        source="session_store",
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


def browser_visible_segments_from_replay_stop(
    replay_stop_request: ReplayStopRequest | None,
) -> list[dict[str, Any]]:
    """Return visible transcript rows from a replay stop request.

    Args:
        replay_stop_request: Optional stop JSON; null means no visible rows were sent.

    Returns:
        Segment dicts with text; empty means backend history is left unchanged.
    """
    # Stop can still be called by older clients that only want task cancellation.
    if replay_stop_request is None:
        return []

    return normalise_browser_visible_segments(replay_stop_request.visible_segments)


def normalise_browser_visible_segments(
    browser_segments: list[BrowserVisibleSegment],
) -> list[dict[str, Any]]:
    """Convert browser-visible segment models into storage-safe dictionaries.

    Args:
        browser_segments: Browser transcript rows; empty means no replay text is visible.

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
    transcript: str,
    *,
    clinical_hints_enabled: bool,
    mercure_event_ids: dict[str, int],
    publish_to_mercure: PublishToMercure,
    generate_clinical_hints: GenerateClinicalHints,
    logger: logging.Logger,
) -> list[dict[str, str]]:
    """Publish summary and optional clinical hints for the browser.

    Args:
        session_id: Browser session UUID; empty would publish to the wrong topic.
        summary: Summary payload; empty still publishes an empty panel state.
        transcript: Transcript text used for assistive hints; empty means no hints.
        clinical_hints_enabled: False means the hints sidebar remains hidden.
        mercure_event_ids: Per-session counters used for EventSource resume.
        publish_to_mercure: Browser event publisher; false means HTTP still returns data.
        generate_clinical_hints: CPU-only hint generator used after summary success.
        logger: Process logger; null is not accepted because failures should be visible.

    Returns:
        Clinical hint list; empty means no sidebar suggestions are shown.
    """
    mercure_event_ids.setdefault(session_id, 0)
    mercure_event_ids[session_id] += 1
    await publish_to_mercure(
        f"scribe/session/{session_id}/summary",
        {
            "type": "summary",
            "session_id": session_id,
            **summary,
        },
        event_id=mercure_event_ids[session_id],
    )

    clinical_hints: list[dict[str, str]] = []
    # Clinical hints are assistive only and appear beside the generated summary.
    if clinical_hints_enabled:
        clinical_hints = generate_clinical_hints(transcript)
        # No hints means the clinician's sidebar stays hidden for this session.
        if clinical_hints:
            mercure_event_ids[session_id] += 1
            hints_delivered = await publish_to_mercure(
                f"scribe/session/{session_id}/hints",
                {
                    "type": "clinical_hints",
                    "session_id": session_id,
                    "hints": clinical_hints,
                },
                event_id=mercure_event_ids[session_id],
            )
            # A failed Mercure publish still leaves hints in the HTTP summary response.
            if not hints_delivered:
                logger.warning(
                    "clinical_hints.publish_failed",
                    extra={"session_id": session_id},
                )

    return clinical_hints
