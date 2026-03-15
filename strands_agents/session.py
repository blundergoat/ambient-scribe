"""
In-memory session store for transcript history.

=============================================================================
WHAT THIS FILE DOES
=============================================================================

Stores transcript segments per session for:
  1. Session history retrieval (GET /session/{id}/history)
  2. Role inference context (accumulated transcript for the Strands agent)
  3. Session export (final transcript on consultation end)

This store manages TRANSCRIPT SEGMENTS (speaker-attributed text with timestamps).

=============================================================================
BOUNDS & EVICTION
=============================================================================

  - MAX_SESSIONS: Hard cap on concurrent sessions. Oldest session evicted
    when exceeded (LRU eviction).
  - SESSION_TTL_SECONDS: Sessions older than this are evicted on access.
  - MAX_SEGMENTS_PER_SESSION: Prevents unbounded growth from long recordings.

=============================================================================
LIMITATIONS (this is a PoC)
=============================================================================

  - IN-MEMORY ONLY: All history is lost when the container restarts.
    For production, use DynamoDB or a database.
"""

from __future__ import annotations

import os
import time
from collections import OrderedDict

MAX_SESSIONS = int(os.environ.get("MAX_SESSIONS", "100"))
SESSION_TTL_SECONDS = int(os.environ.get("SESSION_TTL_SECONDS", "7200"))
MAX_SEGMENTS_PER_SESSION = 5000  # ~4 hours at 5s chunks


class _SessionData:
    """Internal state for a single session."""

    __slots__ = ("segments", "created_at", "last_accessed_at")

    def __init__(self) -> None:
        self.segments: list[dict] = []
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
        """
        session = self._get_or_create(session_id)
        session.segments = list(segments[:self._max_segments])
        session.last_accessed_at = time.monotonic()

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

        session.last_accessed_at = time.monotonic()
        self._sessions.move_to_end(session_id)

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

    def get_transcript_text(self, session_id: str, max_chars: int = 2000) -> str:
        """Return the accumulated transcript as plain text (for role inference context).

        Args:
            session_id: The session UUID
            max_chars: Maximum characters to return (last N chars)

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
        if len(full_text) > max_chars:
            return full_text[-max_chars:]
        return full_text

    def cleanup(self, session_id: str) -> None:
        """Remove a session's data.

        Called when a WebSocket disconnects to prevent memory leaks.

        Args:
            session_id: The session UUID to clean up.
        """
        self._sessions.pop(session_id, None)

    @property
    def session_count(self) -> int:
        """Number of active sessions."""
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
            sid for sid, s in self._sessions.items()
            if (now - s.last_accessed_at) > self._ttl_seconds
        ]
        for sid in expired:
            self._sessions.pop(sid, None)
