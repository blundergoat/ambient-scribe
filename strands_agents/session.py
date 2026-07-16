"""
In-memory transcript history for live scribe sessions.

The browser history view, role inference, exports, and summaries all read this
store while the process is alive. It keeps local development simple, but users
lose stored transcript history after restart unless `SESSION_STORAGE=sqlite`
is selected.
"""

from __future__ import annotations

import os
import time
from collections import OrderedDict

MAX_SESSIONS = int(os.environ.get("MAX_SESSIONS", "100"))
SESSION_TTL_SECONDS = int(os.environ.get("SESSION_TTL_SECONDS", "7200"))
MAX_SEGMENTS_PER_SESSION = 5000  # ~4 hours at 5s chunks
_TRANSCRIPT_ELLIPSIS = "\n...\n"


def _truncate_transcript_text(full_text: str, max_chars: int) -> str:
    """Return a transcript preview no longer than max_chars."""
    if max_chars <= 0:
        return ""
    if len(full_text) <= max_chars:
        return full_text
    if max_chars <= len(_TRANSCRIPT_ELLIPSIS):
        return full_text[:max_chars]

    if max_chars <= 500 + len(_TRANSCRIPT_ELLIPSIS):
        first_size = (max_chars - len(_TRANSCRIPT_ELLIPSIS)) // 2
    else:
        first_size = 500
    last_size = max_chars - len(_TRANSCRIPT_ELLIPSIS) - first_size

    return full_text[:first_size] + _TRANSCRIPT_ELLIPSIS + full_text[-last_size:]


class _SessionData:
    """Internal state for a single session."""

    __slots__ = (
        "segments",
        "corrected_segments",
        "created_at",
        "last_accessed_at",
    )

    def __init__(self) -> None:
        self.segments: list[dict] = []
        self.corrected_segments: list[dict] = []
        self.created_at: float = time.monotonic()
        self.last_accessed_at: float = self.created_at


class SessionStore:
    """In-memory transcript storage with TTL and LRU eviction.

    One instance shared across all requests.
    """

    def __init__(
        self,
        max_sessions: int = MAX_SESSIONS,
        ttl_seconds: int = SESSION_TTL_SECONDS,
        max_segments: int = MAX_SEGMENTS_PER_SESSION,
    ) -> None:
        self._sessions: OrderedDict[str, _SessionData] = OrderedDict()
        self._max_sessions = max_sessions
        self._ttl_seconds = ttl_seconds
        self._max_segments = max_segments

    def append_segment(self, session_id: str, segment: dict) -> None:
        """Append a transcript segment to the session.

        Args:
            session_id: The session UUID
            segment: A segment dict with speaker_id, text, start, end fields
        """
        session = self._get_or_create(session_id)
        if len(session.segments) < self._max_segments:
            session.segments.append(segment)
        session.last_accessed_at = time.monotonic()

    def replace_segments(self, session_id: str, segments: list[dict]) -> None:
        """Replace the stored transcript for a session.

        Useful after a final transcription pass where the caller already has
        the full deduplicated transcript in memory.

        Args:
            session_id: Recording session whose visible transcript is replaced.
            segments: Final transcript lines; empty clears the browser history.
        """
        session = self._get_or_create(session_id)
        session.segments = list(segments[: self._max_segments])
        session.last_accessed_at = time.monotonic()

    def replace_corrected_segments(self, session_id: str, segments: list[dict]) -> None:
        """Replace the post-visit corrected transcript for a session.

        Corrected rows are stored separately from the browser-visible live
        transcript so replay/history consumers keep seeing the preview rows
        until a caller explicitly opts into corrected output.

        Args:
            session_id: Recording session whose corrected transcript is replaced.
            segments: Corrected transcript rows; empty clears the corrected artifact.
        """
        session = self._get_or_create(session_id)
        session.corrected_segments = list(segments[: self._max_segments])
        session.last_accessed_at = time.monotonic()

    def merge_browser_segments(self, session_id: str, segments: list[dict]) -> dict:
        """Merge browser-visible rows into stored history without shrinking it.

        Summary requests carry the rows the clinician sees. Rows the browser
        lacks (finalize flush, filtered blanks) stay stored, matched rows take
        the browser role only while no automatic exception owns them, and rows
        the server never emitted are not appended to a populated history. An
        empty store falls back to insert semantics so a reconnecting browser
        can still restore its transcript.

        Args:
            session_id: Recording UUID the browser is summarizing.
            segments: Browser-visible rows; empty changes nothing.

        Returns:
            Counts for the summary path to log: `matched`, `unknown`, `restored`.
        """
        session = self._sessions.get(session_id)
        # A session with no stored rows is the browser-restore workflow: the
        # browser holds the only copy, so inserts are the correct behavior.
        if session is None or not session.segments:
            self.replace_segments(session_id, segments)
            return {"matched": 0, "unknown": 0, "restored": True}

        stored_by_id = {
            str(stored.get("segment_id", "")): stored
            for stored in session.segments
            if str(stored.get("segment_id", "")) != ""
        }

        matched = 0
        unknown = 0
        for segment in segments:
            segment_id = str(segment.get("segment_id", ""))
            stored = stored_by_id.get(segment_id) if segment_id else None
            # Rows without identity or never emitted cannot be safely merged.
            if stored is None:
                unknown += 1
                continue

            matched += 1
            # Auto-excepted rows keep their server-side label.
            if stored.get("role_source") is not None:
                continue
            stored["role"] = segment.get("role")

        session.last_accessed_at = time.monotonic()
        return {"matched": matched, "unknown": unknown, "restored": False}

    def apply_role_mapping(self, session_id: str, mapping: dict[str, str]) -> None:
        """Annotate stored segments with their inferred roles.

        Args:
            session_id: The session UUID.
            mapping: Speaker-to-role mapping, e.g. {"spk_0": "DOCTOR"}.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return

        if self._is_expired(session):
            self._sessions.pop(session_id, None)
            return

        for segment in session.segments:
            speaker_id = segment.get("speaker_id")
            if isinstance(speaker_id, str) and speaker_id in mapping:
                segment["role"] = mapping[speaker_id]
                # A fresh mapping outdates any automatic row exception; the
                # exception lane re-judges rows right after this call.
                segment.pop("role_source", None)

        session.last_accessed_at = time.monotonic()
        self._sessions.move_to_end(session_id)

    def set_auto_row_roles(self, session_id: str, row_roles: dict[str, str]) -> None:
        """Apply automatic row-level role exceptions to stored rows.

        These are the cue-lane judgments for rows whose text contradicts their
        mapped speaker role. They are derived state: recomputed after every
        mapping application.

        Args:
            session_id: Recording session whose rows are re-judged.
            row_roles: `segment_id -> role` exceptions; empty changes nothing.
        """
        # No exceptions means every visible row agrees with its speaker role.
        if not row_roles:
            return

        session = self._sessions.get(session_id)
        # Unknown or expired sessions have no visible rows to re-judge.
        if session is None or self._is_expired(session):
            return

        for segment in session.segments:
            segment_id = str(segment.get("segment_id", ""))
            auto_role = row_roles.get(segment_id)
            # Rows without an exception keep their mapped speaker role.
            if auto_role is None:
                continue

            segment["role"] = auto_role
            # The source marker lets the UI show this label as automatic.
            segment["role_source"] = "auto_row"

        session.last_accessed_at = time.monotonic()

    def get_corrected_segments(self, session_id: str) -> list[dict]:
        """Return post-visit corrected rows without changing live history.

        Args:
            session_id: Recording UUID whose corrected transcript is requested.

        Returns:
            Corrected segment rows in chronological order; empty means no
            corrected transcript has been stored for this session.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return []
        if self._is_expired(session):
            self._sessions.pop(session_id, None)
            return []
        session.last_accessed_at = time.monotonic()
        self._sessions.move_to_end(session_id)
        return list(session.corrected_segments)

    def get_corrected_transcript_text(
        self, session_id: str, max_chars: int = 3500
    ) -> str:
        """Return corrected transcript text for high-accuracy summary input.

        Args:
            session_id: Recording UUID whose corrected transcript is requested.
            max_chars: Maximum characters; `0` returns empty context.

        Returns:
            Role-attributed corrected transcript text, or empty when no
            corrected transcript exists.
        """
        segments = self.get_corrected_segments(session_id)
        lines = []
        for seg in segments:
            speaker = seg.get("role", seg.get("speaker_id", "UNKNOWN"))
            text = seg.get("text", "")
            lines.append(f"[{speaker}] {text}")

        full_text = "\n".join(lines)
        return _truncate_transcript_text(full_text, max_chars)

    def get_segments(self, session_id: str) -> list[dict]:
        """Return all segments for a session.

        Args:
            session_id: The session UUID

        Returns:
            List of segment dicts in chronological order.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return []
        if self._is_expired(session):
            self._sessions.pop(session_id, None)
            return []
        session.last_accessed_at = time.monotonic()
        self._sessions.move_to_end(session_id)
        return list(session.segments)

    def get_transcript_text(self, session_id: str, max_chars: int = 3500) -> str:
        """Return the accumulated transcript as plain text (for role inference context).

        Returns opening and recent context separated by an ellipsis marker while
        respecting max_chars.

        Args:
            session_id: The session UUID
            max_chars: Maximum characters to return (first 500 + last N)

        Returns:
            Plain text transcript with speaker labels.
        """
        segments = self.get_segments(session_id)
        lines = []
        for seg in segments:
            speaker = seg.get("role", seg.get("speaker_id", "UNKNOWN"))
            text = seg.get("text", "")
            lines.append(f"[{speaker}] {text}")

        full_text = "\n".join(lines)
        return _truncate_transcript_text(full_text, max_chars)

    def cleanup(self, session_id: str) -> None:
        """Remove a session's data.

        Called when a WebSocket disconnects to prevent memory leaks.

        Args:
            session_id: The session UUID to clean up.
        """
        self._sessions.pop(session_id, None)

    @property
    def session_count(self) -> int:
        """Count transcript histories still available to browser views.

        Returns:
            Number of sessions in memory; `0` means no history can be restored.
        """
        return len(self._sessions)

    def _get_or_create(self, session_id: str) -> _SessionData:
        """Get an existing session or create a new one, enforcing limits."""
        if session_id in self._sessions:
            self._sessions.move_to_end(session_id)
            return self._sessions[session_id]

        # Evict expired sessions first
        self._evict_expired()

        # Evict oldest if at capacity
        while len(self._sessions) >= self._max_sessions:
            self._sessions.popitem(last=False)

        session = _SessionData()
        self._sessions[session_id] = session
        return session

    def _is_expired(self, session: _SessionData) -> bool:
        """Check if a session has exceeded its TTL."""
        return (time.monotonic() - session.last_accessed_at) > self._ttl_seconds

    def _evict_expired(self) -> None:
        """Remove all expired sessions."""
        now = time.monotonic()
        expired = [
            sid
            for sid, s in self._sessions.items()
            if (now - s.last_accessed_at) > self._ttl_seconds
        ]
        for sid in expired:
            self._sessions.pop(sid, None)
