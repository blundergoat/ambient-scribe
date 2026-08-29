"""
In-memory transcript history for live scribe sessions.

The browser history view, role inference, exports, and summaries all read this store while the process is alive.

It keeps local development simple, with one trade-off worth knowing: a restart loses every stored transcript, so a
clinician who reconnects afterwards finds nothing to restore. Selecting `SESSION_STORAGE=sqlite` swaps in the persistent
backend instead.
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
    """Return a transcript preview no longer than max_chars.

    A long consultation cannot fit in an agent prompt, so the opening and the most recent speech are kept and the middle
    is replaced by a visible marker. The marker is deliberately visible, so the agent is never handed a gap that reads as
    continuous speech.

    Args:
        full_text: Complete role-attributed transcript; empty returns empty.
        max_chars: Hard character ceiling; zero or lower returns empty, which leaves the agent with no context at all.

    Returns:
        Text within the ceiling, with `\\n...\\n` marking the removed middle whenever anything was dropped.
    """
    # A caller asking for no characters wants no context, so nothing is guessed on its behalf.
    if max_chars <= 0:
        return ""
    # A transcript already inside the ceiling is returned whole, which is the ordinary short-visit case.
    if len(full_text) <= max_chars:
        return full_text
    # A ceiling too small to hold even the marker cannot show both ends, so it degrades to a plain head truncation.
    if max_chars <= len(_TRANSCRIPT_ELLIPSIS):
        return full_text[:max_chars]

    # Under a small ceiling the head and tail split the budget evenly; above it the opening is capped at 500 characters,
    # so every additional character of budget goes to the most recent speech rather than the start of the visit.
    if max_chars <= 500 + len(_TRANSCRIPT_ELLIPSIS):
        first_size = (max_chars - len(_TRANSCRIPT_ELLIPSIS)) // 2
    else:
        first_size = 500
    last_size = max_chars - len(_TRANSCRIPT_ELLIPSIS) - first_size

    return full_text[:first_size] + _TRANSCRIPT_ELLIPSIS + full_text[-last_size:]


class _SessionData:
    """One recording's stored rows and access times.

    Live rows and corrected rows are held separately, so a post-visit correction never overwrites the transcript the
    clinician actually watched. `last_accessed_at` is what `SessionStore` expires against; `created_at` is recorded for
    inspection only and nothing reads it.
    """

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

    One instance is shared across every request, so it is the single answer to "what did this consultation say?" for as
    long as the process lives.

    Two bounds keep a long-running service from growing without limit: sessions expire after `SESSION_TTL_SECONDS`
    without access, and the oldest is evicted once `MAX_SESSIONS` is reached. Either one means a clinician returning to
    an old recording finds nothing to restore.
    """

    def __init__(
        self,
        max_sessions: int = MAX_SESSIONS,
        ttl_seconds: int = SESSION_TTL_SECONDS,
        max_segments: int = MAX_SEGMENTS_PER_SESSION,
    ) -> None:
        """Create the shared store with its capacity and expiry bounds.

        Args:
            max_sessions: Recordings held before the least recently used one is evicted.
            ttl_seconds: Idle seconds after which a recording stops being restorable.
            max_segments: Rows kept per recording; further rows are dropped rather than growing the visit without bound.
        """
        self._sessions: OrderedDict[str, _SessionData] = OrderedDict()
        self._max_sessions = max_sessions
        self._ttl_seconds = ttl_seconds
        self._max_segments = max_segments

    def append_segment(self, session_id: str, segment: dict) -> None:
        """Append one newly visible transcript line to a recording.

        Called as each stable row is published, so the history view can replay exactly what the clinician saw.

        Args:
            session_id: Recording UUID whose history receives the row.
            segment: Row payload with speaker_id, text, start, and end; empty text still stores timing and speaker.
        """
        session = self._get_or_create(session_id)
        # Past the per-session cap the row is dropped rather than stored, so one very long visit cannot exhaust memory.
        if len(session.segments) < self._max_segments:
            session.segments.append(segment)
        session.last_accessed_at = time.monotonic()

    def replace_segments(self, session_id: str, segments: list[dict]) -> None:
        """Replace the stored transcript for a session.

        Used after a final transcription pass, when the caller already holds the full deduplicated transcript and the
        incremental rows streamed during the visit are no longer the best version.

        Args:
            session_id: Recording session whose visible transcript is replaced.
            segments: Final transcript lines; empty clears the browser history.
        """
        session = self._get_or_create(session_id)
        session.segments = list(segments[: self._max_segments])
        session.last_accessed_at = time.monotonic()

    def replace_corrected_segments(self, session_id: str, segments: list[dict]) -> None:
        """Replace the post-visit corrected transcript for a session.

        Corrected rows are stored apart from the browser-visible live transcript, so replay and history consumers keep
        showing the preview rows until a caller explicitly opts into corrected output.

        Args:
            session_id: Recording session whose corrected transcript is replaced.
            segments: Corrected transcript rows; empty clears the corrected artifact and leaves only live rows.
        """
        session = self._get_or_create(session_id)
        session.corrected_segments = list(segments[: self._max_segments])
        session.last_accessed_at = time.monotonic()

    def merge_browser_segments(self, session_id: str, segments: list[dict]) -> dict:
        """Merge browser-visible rows into stored history without shrinking it.

        A summary request carries the rows the clinician can see, which is not always the whole visit. Merging rather
        than replacing keeps the parts the browser never received:

        - rows the browser lacks, such as the finalize flush or filtered blanks, stay stored;
        - matched rows take the browser's role only while no automatic exception owns them; and
        - rows the server never emitted are not appended to a populated history.

        An empty store is the one exception, and falls back to insert semantics so a reconnecting browser can restore
        the transcript it is still holding.

        Args:
            session_id: Recording UUID the browser is summarizing.
            segments: Browser-visible rows; empty changes nothing.

        Returns:
            Counts for the summary path to log: `matched`, `unknown`, `restored`.
        """
        session = self._sessions.get(session_id)
        # No stored rows means this is the browser-restore workflow: the browser holds the only copy of the visit,
        # so its rows are inserted rather than merged against nothing.
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
        # Each browser row is reconciled against stored history one at a time, so one unknown row cannot void the rest.
        for segment in segments:
            segment_id = str(segment.get("segment_id", ""))
            stored = stored_by_id.get(segment_id) if segment_id else None
            # A row with no identity, or one the server never emitted, cannot be matched safely, so it is counted instead.
            if stored is None:
                unknown += 1
                continue

            matched += 1
            # A row carrying role_source was labelled by the cue lane, which judged that row's own wording.
            # The browser only knows the speaker-level mapping, so its role must not overwrite that narrower judgement.
            if stored.get("role_source") is not None:
                continue
            stored["role"] = segment.get("role")

        session.last_accessed_at = time.monotonic()
        return {"matched": matched, "unknown": unknown, "restored": False}

    def apply_role_mapping(self, session_id: str, mapping: dict[str, str]) -> None:
        """Annotate stored segments with their inferred roles.

        Called whenever role inference commits a mapping, so the history view shows the same Doctor and Patient labels
        the live transcript does.

        Args:
            session_id: Recording UUID whose visible labels should change.
            mapping: Speaker-to-role mapping, such as `{"spk_0": "DOCTOR"}`; empty leaves every stored label alone.
        """
        session = self._sessions.get(session_id)
        # An unknown session was already evicted or never existed, so there are no rows to relabel.
        if session is None:
            return

        # An expired session is dropped rather than relabelled, because it can no longer be restored anyway.
        if self._is_expired(session):
            self._sessions.pop(session_id, None)
            return

        # Every stored row is checked, so one mapping updates the whole visit at once.
        for segment in session.segments:
            speaker_id = segment.get("speaker_id")
            # Only rows whose speaker appears in this mapping change; the rest keep whatever label they already had.
            if isinstance(speaker_id, str) and speaker_id in mapping:
                segment["role"] = mapping[speaker_id]
                # A fresh mapping retires any automatic row exception, which the exception lane re-judges right after this call.
                segment.pop("role_source", None)

        session.last_accessed_at = time.monotonic()
        self._sessions.move_to_end(session_id)

    def set_auto_row_roles(self, session_id: str, row_roles: dict[str, str]) -> None:
        """Apply automatic row-level role exceptions to stored rows.

        These are the cue-lane judgements for rows whose wording contradicts their mapped speaker, such as a line that
        reads as the patient speaking inside a run attributed to the doctor. They are derived state, recomputed after
        every mapping application rather than accumulated.

        Args:
            session_id: Recording session whose rows are re-judged.
            row_roles: `segment_id -> role` exceptions; empty changes nothing.
        """
        # No exceptions means every visible row already agrees with its speaker's role, so there is nothing to override.
        if not row_roles:
            return

        session = self._sessions.get(session_id)
        # An unknown or expired session has no visible rows left to re-judge.
        if session is None or self._is_expired(session):
            return

        # Every stored row is offered its exception, because the cue lane keys by row rather than by speaker.
        for segment in session.segments:
            segment_id = str(segment.get("segment_id", ""))
            auto_role = row_roles.get(segment_id)
            # A row with no exception keeps the role its speaker mapping gave it.
            if auto_role is None:
                continue

            segment["role"] = auto_role
            # The source marker is what lets the UI present this label as automatic rather than clinician-confirmed.
            segment["role_source"] = "auto_row"

        session.last_accessed_at = time.monotonic()

    def get_corrected_segments(self, session_id: str) -> list[dict]:
        """Return post-visit corrected rows without changing live history.

        Args:
            session_id: Recording UUID whose corrected transcript is requested.

        Returns:
            Corrected segment rows in chronological order; empty means no corrected transcript has been stored, so the
            caller falls back to the live rows.
        """
        session = self._sessions.get(session_id)
        # An unknown session has nothing to return, which the caller reads as no corrected artifact.
        if session is None:
            return []
        # An expired session is dropped on read, so a stale visit is never served as if it were current.
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

        Preferred over the live text when a corrected artifact exists, because the note is only as good as the wording
        it was written from.

        Args:
            session_id: Recording UUID whose corrected transcript is requested.
            max_chars: Maximum characters; `0` returns empty context, so the agent is given nothing rather than a fragment.

        Returns:
            Role-attributed corrected transcript text, or empty when no corrected transcript exists.
        """
        segments = self.get_corrected_segments(session_id)
        lines = []
        # Each corrected row contributes one role-aware line, so the agent can tell who said what.
        for segment in segments:
            speaker = segment.get("role", segment.get("speaker_id", "UNKNOWN"))
            text = segment.get("text", "")
            lines.append(f"[{speaker}] {text}")

        full_text = "\n".join(lines)
        return _truncate_transcript_text(full_text, max_chars)

    def get_segments(self, session_id: str) -> list[dict]:
        """Return every stored row for a recording, in display order.

        This is what the history view renders when a clinician reopens a visit.

        Args:
            session_id: Recording UUID being restored or summarized.

        Returns:
            Segment rows in chronological order; empty means the visit is unknown or has expired, and the UI shows
            nothing to restore.
        """
        session = self._sessions.get(session_id)
        # An unknown session has nothing to return, which the caller reads as no transcript.
        if session is None:
            return []
        # An expired session is dropped on read, so a stale visit is never served as if it were current.
        if self._is_expired(session):
            self._sessions.pop(session_id, None)
            return []
        session.last_accessed_at = time.monotonic()
        self._sessions.move_to_end(session_id)
        return list(session.segments)

    def get_transcript_text(self, session_id: str, max_chars: int = 3500) -> str:
        """Return the accumulated transcript as role-attributed plain text.

        This is the context the role and summary agents read, trimmed to opening plus recent speech with an ellipsis
        marker between them whenever the visit does not fit.

        Args:
            session_id: Recording UUID whose transcript feeds role or summary agents.
            max_chars: Maximum characters; `0` returns empty context, so the agent is given nothing rather than a fragment.

        Returns:
            Plain transcript text with speaker labels; empty means the agent should not infer content for this session.
        """
        segments = self.get_segments(session_id)
        lines = []
        # Each stored row contributes one role-aware line, so the agent can tell who said what.
        for segment in segments:
            speaker = segment.get("role", segment.get("speaker_id", "UNKNOWN"))
            text = segment.get("text", "")
            lines.append(f"[{speaker}] {text}")

        full_text = "\n".join(lines)
        return _truncate_transcript_text(full_text, max_chars)

    def cleanup(self, session_id: str) -> None:
        """Remove a session's data.

        Called when a WebSocket disconnects, so a finished visit stops occupying memory rather than waiting out its TTL.

        Args:
            session_id: Recording UUID to clean up; an unknown ID leaves the store unchanged.
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
        """Return this recording's state, creating it and enforcing capacity first.

        Args:
            session_id: Recording UUID being written to.

        Returns:
            Existing state moved to the most-recently-used end, or a fresh empty one for a visit's first row.
        """
        # A known session is only moved to the recent end, so an active visit is never evicted out from under a clinician.
        if session_id in self._sessions:
            self._sessions.move_to_end(session_id)
            return self._sessions[session_id]

        self._evict_expired()

        # Capacity is enforced oldest-first, so the visit evicted is always the one least recently looked at.
        while len(self._sessions) >= self._max_sessions:
            self._sessions.popitem(last=False)

        session = _SessionData()
        self._sessions[session_id] = session
        return session

    def _is_expired(self, session: _SessionData) -> bool:
        """Report whether a recording has sat idle past its TTL.

        Args:
            session: Stored state for one recording.

        Returns:
            True when the visit can no longer be restored and should be dropped on the next read.
        """
        return (time.monotonic() - session.last_accessed_at) > self._ttl_seconds

    def _evict_expired(self) -> None:
        """Drop every recording that has sat idle past its TTL.

        Runs before a new visit is admitted, so idle consultations free their space rather than pushing out active ones.
        """
        now = time.monotonic()
        expired_session_ids = [
            session_id
            for session_id, session in self._sessions.items()
            if (now - session.last_accessed_at) > self._ttl_seconds
        ]
        # Removal is a second pass, because mutating the store while iterating it would skip entries.
        for session_id in expired_session_ids:
            self._sessions.pop(session_id, None)
