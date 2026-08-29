"""
Persistent transcript storage contracts and SQLite implementation.

The API talks to this protocol whether a transcript lives in memory or on disk, so route code never has to know which.

SQLite keeps the clinician's visible rows and role labels across process restarts. That is what lets a browser reconnect
after a container restart and still restore the consultation it was showing, and still generate a note from it.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import Protocol, runtime_checkable


@runtime_checkable
class StorageBackend(Protocol):
    """
    Protocol for transcript storage backends.

    Implement this when a backend can serve the same browser history, role-label, and summary workflows as the in-memory
    store. Two rules hold across every method:

    - An empty result always means the UI has no transcript to restore for that session, never that a lookup failed.
    - Live rows and corrected rows are separate artifacts; writing one must never disturb the other.
    """

    def append_segment(self, session_id: str, segment: dict) -> None:
        """Append one newly visible transcript line to persistent history.

        Args:
            session_id: Recording UUID whose history receives the row.
            segment: Row payload; empty text still stores timing and speaker context.
        """
        ...

    def replace_segments(self, session_id: str, segments: list[dict]) -> None:
        """Replace every live row for a session, such as after final transcription.

        Args:
            session_id: Recording UUID whose stored history is replaced.
            segments: Final transcript rows; empty clears the visible history.
        """
        ...

    def replace_corrected_segments(self, session_id: str, segments: list[dict]) -> None:
        """Replace the post-visit corrected rows, leaving live history untouched.

        Args:
            session_id: Recording UUID whose corrected transcript is replaced.
            segments: Corrected rows; empty clears the corrected artifact and leaves only live rows.
        """
        ...

    def merge_browser_segments(self, session_id: str, segments: list[dict]) -> dict:
        """Fold the rows the browser is showing into stored history without shrinking it.

        Args:
            session_id: Recording UUID the browser is summarizing.
            segments: Browser-visible rows; empty changes nothing.

        Returns:
            Counts for the caller to log: `matched`, `unknown`, `restored`.
        """
        ...

    def get_segments(self, session_id: str) -> list[dict]:
        """Return live rows in the order the browser should display them.

        Args:
            session_id: Recording UUID being restored or summarized.

        Returns:
            Segment rows in display order; empty means there is no transcript to restore.
        """
        ...

    def get_transcript_text(self, session_id: str, max_chars: int = 3500) -> str:
        """Return live rows as role-attributed plain text for an agent prompt.

        Args:
            session_id: Recording UUID whose transcript feeds an agent.
            max_chars: Character ceiling; `0` returns empty context rather than a fragment.

        Returns:
            Plain transcript text; empty means the agent should infer nothing for this session.
        """
        ...

    def get_corrected_segments(self, session_id: str) -> list[dict]:
        """Return the post-visit corrected rows for this session.

        Args:
            session_id: Recording UUID whose corrected transcript is requested.

        Returns:
            Corrected rows in display order; empty means no corrected artifact exists, so callers use live rows.
        """
        ...

    def get_corrected_transcript_text(
        self, session_id: str, max_chars: int = 3500
    ) -> str:
        """Return corrected rows as role-attributed plain text for an agent prompt.

        Args:
            session_id: Recording UUID whose corrected transcript feeds summary generation.
            max_chars: Character ceiling; `0` returns empty context rather than a fragment.

        Returns:
            Role-attributed corrected text; empty means no corrected transcript is available.
        """
        ...

    def apply_role_mapping(self, session_id: str, mapping: dict[str, str]) -> None:
        """Persist a speaker-to-role mapping so restored history matches the live transcript.

        Args:
            session_id: Recording UUID whose visible labels change.
            mapping: Speaker-to-role map such as `{"spk_0": "DOCTOR"}`; empty leaves stored labels alone.
        """
        ...

    def set_auto_row_roles(self, session_id: str, row_roles: dict[str, str]) -> None:
        """Apply automatic per-row role exceptions on top of the speaker mapping.

        Args:
            session_id: Recording session whose rows are re-judged.
            row_roles: `segment_id -> role` exceptions; empty changes nothing.
        """
        ...

    def cleanup(self, session_id: str) -> None:
        """Remove one session's stored transcript once it is no longer needed.

        Args:
            session_id: Recording UUID to remove; an unknown ID leaves storage unchanged.
        """
        ...

    @property
    def session_count(self) -> int:
        """Count transcript histories currently available to restore.

        Returns:
            Number of stored sessions; `0` means no browser could restore a visit.
        """
        ...


SESSION_DB_PATH = os.environ.get("SESSION_DB_PATH", "/data/sessions.db")
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


class SqliteBackend:
    """
    Persistent transcript storage backed by one SQLite file.

    Use when transcript histories must survive a container or process restart, which is the difference between a
    clinician reconnecting to their consultation and losing it.

    Raw speaker labels, role labels, and timestamps are stored in display order, so a restored session shows exactly the
    rows the browser had before. One SQLite connection is shared across the API's worker threads, so every read and write
    takes this instance's lock before touching it.
    """

    def __init__(self, db_path: str | None = None) -> None:
        """Open the session database and bring its schema up to date.

        Args:
            db_path: SQLite file to use; null falls back to `SESSION_DB_PATH`, which is the deployed data volume.
        """
        self._db_path = db_path or SESSION_DB_PATH
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            self._db_path,
            check_same_thread=False,
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()

    def _create_tables(self) -> None:
        """Create the sessions, segments, and corrected-segments tables if they are missing.

        Runs on every startup, so a fresh volume and an existing one both end up with the same schema.
        """
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    last_accessed_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS segments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    speaker_id TEXT,
                    text TEXT,
                    start REAL,
                    end REAL,
                    is_interim INTEGER DEFAULT 0,
                    role TEXT,
                    position INTEGER NOT NULL,
                    segment_id TEXT,
                    role_source TEXT,
                    confidence REAL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS corrected_segments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    speaker_id TEXT,
                    text TEXT,
                    start REAL,
                    end REAL,
                    is_interim INTEGER DEFAULT 0,
                    role TEXT,
                    position INTEGER NOT NULL,
                    segment_id TEXT,
                    role_source TEXT,
                    source TEXT,
                    source_model TEXT,
                    confidence REAL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_segments_session_position
                    ON segments(session_id, position);

                CREATE INDEX IF NOT EXISTS idx_corrected_segments_session_position
                    ON corrected_segments(session_id, position);
            """)
            # A database created before row corrections or row confidence lacks the newer columns, so each is added here.
            for add_column_sql in (
                "ALTER TABLE segments ADD COLUMN segment_id TEXT",
                "ALTER TABLE segments ADD COLUMN role_source TEXT",
                "ALTER TABLE segments ADD COLUMN confidence REAL",
                "ALTER TABLE corrected_segments ADD COLUMN confidence REAL",
            ):
                try:
                    self._conn.execute(add_column_sql)
                # Example: a developer restarts against a volume that already has these columns from a previous run.
                # SQLite reports "duplicate column", which means the upgrade is already done and startup simply continues.
                except sqlite3.OperationalError:
                    pass
            self._conn.commit()

    def _ensure_session(self, session_id: str) -> None:
        """Create the session row if it is missing, and stamp it as just accessed.

        Called before every insert, because a segment row cannot exist without its parent session under the foreign key.

        Args:
            session_id: Recording UUID being written to.
        """
        now = time.time()
        self._conn.execute(
            """
            INSERT INTO sessions (id, created_at, last_accessed_at)
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET last_accessed_at = excluded.last_accessed_at
            """,
            (session_id, now, now),
        )

    def _next_position(self, session_id: str) -> int:
        """Return the next display position for a session's segments.

        Position is what preserves the order the clinician heard the conversation in, independent of insertion timing.

        Args:
            session_id: Recording UUID whose row order is being extended.

        Returns:
            Next free position; `0` for a session with no stored rows yet.
        """
        row = self._conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM segments WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return row[0] if row else 0

    def append_segment(self, session_id: str, segment: dict) -> None:
        """Append one newly visible transcript line to persistent history.

        Called as each stable row is published, so a restored session shows the same rows in the same order.

        Args:
            session_id: Recording UUID whose browser history receives the segment.
            segment: Segment payload; empty text still stores timing and speaker context.
        """
        with self._lock:
            self._ensure_session(session_id)
            position = self._next_position(session_id)
            self._conn.execute(
                """
                INSERT INTO segments (session_id, speaker_id, text, start, end, is_interim, role, position, segment_id, confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    segment.get("speaker_id"),
                    segment.get("text"),
                    segment.get("start"),
                    segment.get("end"),
                    1 if segment.get("is_interim") else 0,
                    segment.get("role"),
                    position,
                    str(segment.get("segment_id", "")) or None,
                    segment.get("confidence"),
                ),
            )
            self._conn.commit()

    def replace_segments(self, session_id: str, segments: list[dict]) -> None:
        """Replace all visible transcript lines after final transcription.

        Used when the whole visit has been re-decoded, so the stored history matches the final text rather than the
        incremental rows that were streamed during the consultation.

        Args:
            session_id: Recording UUID whose stored history should be replaced.
            segments: Final transcript payloads; empty clears the visible history entirely.
        """
        with self._lock:
            self._ensure_session(session_id)
            self._conn.execute(
                "DELETE FROM segments WHERE session_id = ?",
                (session_id,),
            )
            # Rows are re-inserted in list order, so the enumerate index becomes the display position.
            for position, segment in enumerate(segments):
                self._conn.execute(
                    """
                    INSERT INTO segments (session_id, speaker_id, text, start, end, is_interim, role, position, segment_id, confidence)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        segment.get("speaker_id"),
                        segment.get("text"),
                        segment.get("start"),
                        segment.get("end"),
                        1 if segment.get("is_interim") else 0,
                        segment.get("role"),
                        position,
                        str(segment.get("segment_id", "")) or None,
                        segment.get("confidence"),
                    ),
                )
            self._conn.commit()

    def replace_corrected_segments(self, session_id: str, segments: list[dict]) -> None:
        """Replace post-visit corrected transcript rows without touching live history.

        The two artifacts are kept apart on purpose: if correction is later found to be wrong, the live rows the
        clinician actually watched are still exactly as they were.

        Args:
            session_id: Recording UUID whose corrected transcript is replaced.
            segments: Corrected transcript payloads; empty clears the corrected artifact and leaves only live rows.
        """
        with self._lock:
            self._ensure_session(session_id)
            self._conn.execute(
                "DELETE FROM corrected_segments WHERE session_id = ?",
                (session_id,),
            )
            # Corrected rows carry extra provenance columns, so the reader can tell which model produced each line.
            for position, segment in enumerate(segments):
                self._conn.execute(
                    """
                    INSERT INTO corrected_segments (
                        session_id,
                        speaker_id,
                        text,
                        start,
                        end,
                        is_interim,
                        role,
                        position,
                        segment_id,
                        role_source,
                        source,
                        source_model,
                        confidence
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        segment.get("speaker_id"),
                        segment.get("text"),
                        segment.get("start"),
                        segment.get("end"),
                        1 if segment.get("is_interim") else 0,
                        segment.get("role"),
                        position,
                        str(segment.get("segment_id", "")) or None,
                        segment.get("role_source"),
                        segment.get("source"),
                        segment.get("source_model"),
                        segment.get("confidence"),
                    ),
                )
            self._conn.commit()

    def merge_browser_segments(self, session_id: str, segments: list[dict]) -> dict:
        """Merge browser-visible rows into stored history without shrinking it.

        A summary request carries the rows the clinician can see, which is not always the whole visit. Merging rather
        than replacing keeps the parts the browser never received:

        - rows the browser lacks, such as the finalize flush or filtered blanks, stay stored;
        - matched rows take the browser's role only while no automatic row exception owns them; and
        - rows the server never emitted are not appended to a populated history.

        An empty store is the one exception, and falls back to insert semantics so a reconnecting browser can restore
        the transcript it is still holding.

        Args:
            session_id: Recording UUID the browser is summarizing.
            segments: Browser-visible rows; empty changes nothing.

        Returns:
            Counts for the summary path to log: `matched`, `unknown`, `restored`.
        """
        with self._lock:
            stored_count_row = self._conn.execute(
                "SELECT COUNT(*) FROM segments WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            store_is_empty = (stored_count_row[0] if stored_count_row else 0) == 0

        # No stored rows means this is the browser-restore workflow: the browser holds the only copy of the visit,
        # so its rows are inserted rather than merged against nothing.
        if store_is_empty:
            self.replace_segments(session_id, segments)
            return {"matched": 0, "unknown": 0, "restored": True}

        matched = 0
        unknown = 0
        with self._lock:
            self._ensure_session(session_id)
            # Each browser row is reconciled against stored history one at a time, so one unknown row cannot void the rest.
            for segment in segments:
                segment_id = str(segment.get("segment_id", ""))
                # A row without identity cannot be matched to stored history, so it is counted rather than guessed at.
                if segment_id == "":
                    unknown += 1
                    continue

                cursor = self._conn.execute(
                    """
                    UPDATE segments SET role = ?
                    WHERE session_id = ? AND segment_id = ? AND role_source IS NULL
                    """,
                    (segment.get("role"), session_id, segment_id),
                )
                # A successful update means the row was stored and carried no server-side label, so the browser's role now applies.
                if cursor.rowcount > 0:
                    matched += 1
                else:
                    known = self._conn.execute(
                        "SELECT 1 FROM segments WHERE session_id = ? AND segment_id = ?",
                        (session_id, segment_id),
                    ).fetchone()
                    # A row that refused the update carries a role_source, meaning the cue lane labelled it from its own wording.
                    #
                    # It still counts as matched, because the transcript does contain that row.
                    # Only its role is not the browser's to set, since the browser knows just the speaker-level mapping.
                    if known is None:
                        unknown += 1
                    else:
                        matched += 1
            self._conn.commit()

        return {"matched": matched, "unknown": unknown, "restored": False}

    def get_segments(self, session_id: str) -> list[dict]:
        """Return transcript lines in the order the browser should display them.

        This is what a reconnecting browser renders, so the shape matches the rows published live during the visit.

        Args:
            session_id: Recording UUID requested by history, replay, or summary flow.

        Returns:
            Segment list; empty means no transcript is available for that session, which the UI shows as nothing to restore.
        """
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT speaker_id, text, start, end, is_interim, role, segment_id, role_source, confidence
                FROM segments
                WHERE session_id = ?
                ORDER BY position
                """,
                (session_id,),
            ).fetchall()

        segments = []
        # Each row becomes one transcript line the browser can restore, with optional keys omitted rather than nulled,
        # so a restored row renders identically to the one that was published live.
        for row in rows:
            seg: dict = {
                "speaker_id": row[0],
                "text": row[1],
                "start": row[2],
                "end": row[3],
                "is_interim": bool(row[4]),
            }
            # A missing role means role inference never labelled this row, so the UI still shows the raw speaker label.
            if row[5] is not None:
                seg["role"] = row[5]
            # Row IDs let the browser and later corrections target one visible line.
            if row[6]:
                seg["segment_id"] = row[6]
            # A set source marker means the cue lane judged this row's own wording, rather than the row simply inheriting
            # its speaker's mapped role. A clinician's own confirmation is tracked separately, on the role state.
            if row[7]:
                seg["role_source"] = row[7]
            # Unmeasured rows omit the key entirely, so they render without confidence styling.
            if row[8] is not None:
                seg["confidence"] = row[8]
            segments.append(seg)
        return segments

    def get_corrected_segments(self, session_id: str) -> list[dict]:
        """Return post-visit corrected rows without changing live history.

        Args:
            session_id: Recording UUID requested by a corrected transcript consumer.

        Returns:
            Corrected segment list; empty means no corrected artifact exists, so the caller falls back to live rows.
        """
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT speaker_id, text, start, end, is_interim, role, segment_id, role_source, source, source_model, confidence
                FROM corrected_segments
                WHERE session_id = ?
                ORDER BY position
                """,
                (session_id,),
            ).fetchall()

        segments = []
        # Corrected rows use the same public shape as live rows, plus optional provenance for the slower ASR pass.
        for row in rows:
            seg: dict = {
                "speaker_id": row[0],
                "text": row[1],
                "start": row[2],
                "end": row[3],
                "is_interim": bool(row[4]),
            }
            # A missing role means this corrected row was never labelled, so it shows its raw speaker label.
            if row[5] is not None:
                seg["role"] = row[5]
            # Row IDs let a note cite one corrected line.
            if row[6]:
                seg["segment_id"] = row[6]
            # A set source marker means the post-visit alignment pass chose this row's role from its own wording.
            if row[7]:
                seg["role_source"] = row[7]
            # Provenance is present only when the correction pass recorded it, so an older artifact stays readable.
            if row[8]:
                seg["source"] = row[8]
            if row[9]:
                seg["source_model"] = row[9]
            # Unmeasured rows omit the key entirely, so they render without confidence styling.
            if row[10] is not None:
                seg["confidence"] = row[10]
            segments.append(seg)
        return segments

    def get_transcript_text(self, session_id: str, max_chars: int = 3500) -> str:
        """Return the accumulated transcript as role-attributed plain text.

        This is the context the role and summary agents read, trimmed to opening plus recent speech with an ellipsis
        marker between them whenever the visit does not fit.

        Args:
            session_id: Recording UUID whose transcript feeds role or summary agents.
            max_chars: Maximum characters; `0` returns empty context, so the agent is given nothing rather than a fragment.

        Returns:
            Plain transcript text; empty means the agent should not infer content for this session.
        """
        segments = self.get_segments(session_id)
        lines = []
        # Each stored segment contributes one role-aware line, so the agent can tell who said what.
        for seg in segments:
            speaker = seg.get("role", seg.get("speaker_id", "UNKNOWN"))
            text = seg.get("text", "")
            lines.append(f"[{speaker}] {text}")

        full_text = "\n".join(lines)
        return _truncate_transcript_text(full_text, max_chars)

    def get_corrected_transcript_text(
        self, session_id: str, max_chars: int = 3500
    ) -> str:
        """Return corrected transcript text for high-accuracy summary input.

        Preferred over the live text when a corrected artifact exists, because the note is only as good as the wording
        it was written from.

        Args:
            session_id: Recording UUID whose corrected transcript feeds summary generation.
            max_chars: Maximum characters; `0` returns empty context to callers.

        Returns:
            Role-attributed corrected transcript text; empty means no corrected transcript is available for that session.
        """
        segments = self.get_corrected_segments(session_id)
        lines = []
        # Each corrected row contributes one role-aware line, matching the live-text shape so prompts stay interchangeable.
        for seg in segments:
            speaker = seg.get("role", seg.get("speaker_id", "UNKNOWN"))
            text = seg.get("text", "")
            lines.append(f"[{speaker}] {text}")

        full_text = "\n".join(lines)
        return _truncate_transcript_text(full_text, max_chars)

    def apply_role_mapping(self, session_id: str, mapping: dict[str, str]) -> None:
        """Persist speaker roles so restored history matches the live transcript.

        Called whenever role inference commits a mapping, so a browser reconnecting later sees the same Doctor and
        Patient labels it was showing before.

        Args:
            session_id: Recording UUID whose visible labels should change.
            mapping: Speaker-to-role map; empty means no stored labels change.
        """
        with self._lock:
            # Each entry relabels every row for that speaker at once, which is what makes the change look instant in the UI.
            #
            # Clearing role_source retires stale automatic row exceptions: those are derived from the old mapping and are
            # re-judged immediately after every mapping application.
            for speaker_id, role in mapping.items():
                self._conn.execute(
                    """
                    UPDATE segments SET role = ?, role_source = NULL
                    WHERE session_id = ? AND speaker_id = ?
                    """,
                    (role, session_id, speaker_id),
                )
            self._conn.commit()

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

        with self._lock:
            # Each exception relabels exactly one visible row and marks it as automatic, so a later mapping can retire it.
            for segment_id, auto_role in row_roles.items():
                self._conn.execute(
                    """
                    UPDATE segments SET role = ?, role_source = 'auto_row'
                    WHERE session_id = ? AND segment_id = ?
                    """,
                    (auto_role, session_id, segment_id),
                )
            self._conn.commit()

    def cleanup(self, session_id: str) -> None:
        """Remove a transcript history when the session is no longer needed.

        Both artifacts go together, so a cleaned-up visit cannot leave corrected rows behind with no live rows to explain them.

        Args:
            session_id: Recording UUID to remove; unknown IDs leave storage unchanged.
        """
        with self._lock:
            self._conn.execute(
                "DELETE FROM corrected_segments WHERE session_id = ?", (session_id,)
            )
            self._conn.execute(
                "DELETE FROM segments WHERE session_id = ?", (session_id,)
            )
            self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            self._conn.commit()

    @property
    def session_count(self) -> int:
        """Count transcript histories available for restore.

        Returns:
            Number of stored sessions; `0` means no persisted history exists, so no browser could restore a visit.
        """
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
        return row[0] if row else 0

    def close(self) -> None:
        """Close SQLite resources when the API process is shutting down.

        Stored transcripts survive the close; only the connection goes away, so the next startup restores the same visits.
        """
        with self._lock:
            self._conn.close()
