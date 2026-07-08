"""
Persistent transcript storage contracts and SQLite implementation.

The API uses this protocol whether transcript history is in memory or on disk.
SQLite keeps user-visible segments and role labels across process restarts so
the browser can restore histories and generate summaries after reconnects.
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

    Implement this when a storage backend can serve the same browser history,
    role-label, and summary workflows as the in-memory store. Empty results
    always mean the UI has no transcript to restore for that session.
    """

    def append_segment(self, session_id: str, segment: dict) -> None: ...
    def replace_segments(self, session_id: str, segments: list[dict]) -> None: ...
    def replace_corrected_segments(
        self, session_id: str, segments: list[dict]
    ) -> None: ...
    def merge_browser_segments(
        self, session_id: str, segments: list[dict]
    ) -> dict: ...
    def get_segments(self, session_id: str) -> list[dict]: ...
    def get_transcript_text(self, session_id: str, max_chars: int = 3500) -> str: ...
    def get_corrected_segments(self, session_id: str) -> list[dict]: ...
    def get_corrected_transcript_text(
        self, session_id: str, max_chars: int = 3500
    ) -> str: ...
    def apply_role_mapping(self, session_id: str, mapping: dict[str, str]) -> None: ...
    def set_row_role(self, session_id: str, segment_id: str, role: str) -> bool: ...
    def set_auto_row_roles(self, session_id: str, row_roles: dict[str, str]) -> None: ...
    def cleanup(self, session_id: str) -> None: ...

    @property
    def session_count(self) -> int: ...


SESSION_DB_PATH = os.environ.get("SESSION_DB_PATH", "/data/sessions.db")
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


class SqliteBackend:
    """
    Persistent transcript storage backed by one SQLite file.

    Use this when transcript histories must survive a container or process
    restart. It stores raw speaker labels, role labels, and timestamps in order
    so the browser can restore the same visible session state.
    """

    def __init__(self, db_path: str | None = None) -> None:
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
        """Create sessions, segments, and row-correction tables if they don't exist."""
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

                CREATE TABLE IF NOT EXISTS row_role_overrides (
                    session_id TEXT NOT NULL,
                    segment_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    PRIMARY KEY (session_id, segment_id),
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
            # Databases created before row corrections or row confidence lack
            # the newer columns; SQLite raises "duplicate column" once they
            # exist, which is fine.
            for add_column_sql in (
                "ALTER TABLE segments ADD COLUMN segment_id TEXT",
                "ALTER TABLE segments ADD COLUMN role_source TEXT",
                "ALTER TABLE segments ADD COLUMN confidence REAL",
                "ALTER TABLE corrected_segments ADD COLUMN confidence REAL",
            ):
                try:
                    self._conn.execute(add_column_sql)
                except sqlite3.OperationalError:
                    pass
            self._conn.commit()

    def _ensure_session(self, session_id: str) -> None:
        """Create a session row if it doesn't exist, update last_accessed_at."""
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
        """Return the next position value for a session's segments."""
        row = self._conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM segments WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return row[0] if row else 0

    def append_segment(self, session_id: str, segment: dict) -> None:
        """Append one newly visible transcript line to persistent history.

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
            # A re-appended row the clinician already corrected keeps their label.
            self._reapply_row_role_overrides(session_id)
            self._conn.commit()

    def replace_segments(self, session_id: str, segments: list[dict]) -> None:
        """Replace all visible transcript lines after final transcription.

        Rows the clinician corrected keep their corrected role by `segment_id`
        across the replace, because finalize and summary flows rebuild rows
        from raw transcript objects without role annotations.

        Args:
            session_id: Recording UUID whose stored history should be replaced.
            segments: Final transcript payloads; empty clears the visible history.
        """
        with self._lock:
            self._ensure_session(session_id)
            self._conn.execute(
                "DELETE FROM segments WHERE session_id = ?",
                (session_id,),
            )
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
            self._reapply_row_role_overrides(session_id)
            self._conn.commit()

    def replace_corrected_segments(self, session_id: str, segments: list[dict]) -> None:
        """Replace post-visit corrected transcript rows without touching live history.

        Args:
            session_id: Recording UUID whose corrected transcript is replaced.
            segments: Corrected transcript payloads; empty clears the corrected artifact.
        """
        with self._lock:
            self._ensure_session(session_id)
            self._conn.execute(
                "DELETE FROM corrected_segments WHERE session_id = ?",
                (session_id,),
            )
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

        Summary requests carry the rows the clinician sees. Rows the browser
        lacks (finalize flush, filtered blanks) stay stored, matched rows take
        the browser role only while no correction or automatic exception owns
        them, and rows the server never emitted are not appended to a
        populated history. An empty store falls back to insert semantics so a
        reconnecting browser can still restore its transcript.

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

        # A session with no stored rows is the browser-restore workflow: the
        # browser holds the only copy, so inserts are the correct behavior.
        if store_is_empty:
            self.replace_segments(session_id, segments)
            return {"matched": 0, "unknown": 0, "restored": True}

        matched = 0
        unknown = 0
        with self._lock:
            self._ensure_session(session_id)
            for segment in segments:
                segment_id = str(segment.get("segment_id", ""))
                # Rows without identity cannot be safely merged into history.
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
                # Corrected/auto rows also count as matched: they exist stored.
                if cursor.rowcount > 0:
                    matched += 1
                else:
                    known = self._conn.execute(
                        "SELECT 1 FROM segments WHERE session_id = ? AND segment_id = ?",
                        (session_id, segment_id),
                    ).fetchone()
                    if known is None:
                        unknown += 1
                    else:
                        matched += 1
            self._conn.commit()

        return {"matched": matched, "unknown": unknown, "restored": False}

    def get_segments(self, session_id: str) -> list[dict]:
        """Return transcript lines in the order the browser should display them.

        Args:
            session_id: Recording UUID requested by history, replay, or summary flow.

        Returns:
            Segment list; empty means no transcript is available for that session.
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
        # Each row becomes one transcript line the browser can restore.
        for row in rows:
            seg: dict = {
                "speaker_id": row[0],
                "text": row[1],
                "start": row[2],
                "end": row[3],
                "is_interim": bool(row[4]),
            }
            # A missing role means the UI still shows the raw speaker label.
            if row[5] is not None:
                seg["role"] = row[5]
            # Row IDs let the browser and corrections target one visible line.
            if row[6]:
                seg["segment_id"] = row[6]
            # The source marker shows which rows carry a human correction.
            if row[7]:
                seg["role_source"] = row[7]
            # Unmeasured rows omit the key so they render exactly as before.
            if row[8] is not None:
                seg["confidence"] = row[8]
            segments.append(seg)
        return segments

    def get_corrected_segments(self, session_id: str) -> list[dict]:
        """Return post-visit corrected rows without changing live history.

        Args:
            session_id: Recording UUID requested by a corrected transcript consumer.

        Returns:
            Corrected segment list; empty means no corrected artifact exists.
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
        # Corrected rows use the same public shape as live rows, plus optional
        # provenance fields for the slower ASR/alignment pass.
        for row in rows:
            seg: dict = {
                "speaker_id": row[0],
                "text": row[1],
                "start": row[2],
                "end": row[3],
                "is_interim": bool(row[4]),
            }
            if row[5] is not None:
                seg["role"] = row[5]
            if row[6]:
                seg["segment_id"] = row[6]
            if row[7]:
                seg["role_source"] = row[7]
            if row[8]:
                seg["source"] = row[8]
            if row[9]:
                seg["source_model"] = row[9]
            # Unmeasured rows omit the key so they render exactly as before.
            if row[10] is not None:
                seg["confidence"] = row[10]
            segments.append(seg)
        return segments

    def get_transcript_text(self, session_id: str, max_chars: int = 3500) -> str:
        """Return the accumulated transcript as plain text (for role inference context).

        Returns opening and recent context separated by an ellipsis marker while
        respecting max_chars.

        Args:
            session_id: Recording UUID whose transcript feeds role or summary agents.
            max_chars: Maximum characters; `0` returns empty context to callers.

        Returns:
            Plain transcript text; empty means the agent should not infer content.
        """
        segments = self.get_segments(session_id)
        lines = []
        # Each stored segment contributes one role-aware line for the agent.
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

        Args:
            session_id: Recording UUID whose corrected transcript feeds summary generation.
            max_chars: Maximum characters; `0` returns empty context to callers.

        Returns:
            Role-attributed corrected transcript text; empty means no corrected
            transcript is available for that session.
        """
        segments = self.get_corrected_segments(session_id)
        lines = []
        # Each corrected row contributes one role-aware line for the agent.
        for seg in segments:
            speaker = seg.get("role", seg.get("speaker_id", "UNKNOWN"))
            text = seg.get("text", "")
            lines.append(f"[{speaker}] {text}")

        full_text = "\n".join(lines)
        return _truncate_transcript_text(full_text, max_chars)

    def apply_role_mapping(self, session_id: str, mapping: dict[str, str]) -> None:
        """Persist speaker roles so restored history matches the live transcript.

        Rows the clinician corrected individually are skipped: a whole-speaker
        mapping must never silently undo a per-row human correction.

        Args:
            session_id: Recording UUID whose visible labels should change.
            mapping: Speaker-to-role map; empty means no stored labels change.
        """
        with self._lock:
            # Each mapping entry updates all matching lines in the user's
            # transcript; clearing role_source outdates stale automatic row
            # exceptions, which are re-judged right after every mapping.
            for speaker_id, role in mapping.items():
                self._conn.execute(
                    """
                    UPDATE segments SET role = ?, role_source = NULL
                    WHERE session_id = ? AND speaker_id = ?
                      AND (role_source IS NULL OR role_source != 'user_row')
                    """,
                    (role, session_id, speaker_id),
                )
            self._conn.commit()

    def set_auto_row_roles(self, session_id: str, row_roles: dict[str, str]) -> None:
        """Apply automatic row-level role exceptions to stored rows.

        These are the cue-lane judgments for rows whose text contradicts their
        mapped speaker role. They are derived state: recomputed after every
        mapping application, and they never touch a row the clinician
        corrected personally.

        Args:
            session_id: Recording session whose rows are re-judged.
            row_roles: `segment_id -> role` exceptions; empty changes nothing.
        """
        # No exceptions means every visible row agrees with its speaker role.
        if not row_roles:
            return

        with self._lock:
            # Each exception re-labels exactly one visible row.
            for segment_id, auto_role in row_roles.items():
                self._conn.execute(
                    """
                    UPDATE segments SET role = ?, role_source = 'auto_row'
                    WHERE session_id = ? AND segment_id = ?
                      AND (role_source IS NULL OR role_source != 'user_row')
                    """,
                    (auto_role, session_id, segment_id),
                )
            self._conn.commit()

    def set_row_role(self, session_id: str, segment_id: str, role: str) -> bool:
        """Record a clinician's per-row role correction and apply it now.

        The correction is keyed by the row's `segment_id`, survives later
        speaker-level role mappings and finalize/summary row replacement, and
        never touches the speaker-scoped mapping.

        Args:
            session_id: Recording UUID the clinician is correcting.
            segment_id: Stable row ID minted at emission; empty cannot target a row.
            role: Corrected role label shown for exactly that row.

        Returns:
            True when the session exists and the correction was recorded;
            False means the browser targeted an unknown session or empty row ID.
        """
        # Empty row IDs cannot identify which visible row the user corrected.
        if not segment_id:
            return False

        with self._lock:
            known_session = self._conn.execute(
                "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            # Unknown sessions have no transcript the user could be seeing.
            if known_session is None:
                return False

            self._conn.execute(
                """
                INSERT INTO row_role_overrides (session_id, segment_id, role)
                VALUES (?, ?, ?)
                ON CONFLICT(session_id, segment_id) DO UPDATE SET role = excluded.role
                """,
                (session_id, segment_id, role),
            )
            self._reapply_row_role_overrides(session_id)
            self._conn.commit()
        return True

    def _reapply_row_role_overrides(self, session_id: str) -> None:
        """Stamp stored rows with their clinician-corrected roles.

        Called inside a held lock after any write that could rebuild or add
        rows, so corrected rows always display the human label.

        Args:
            session_id: Recording UUID whose corrections should be re-applied.
        """
        self._conn.execute(
            """
            UPDATE segments
            SET role = (
                    SELECT overrides.role FROM row_role_overrides AS overrides
                    WHERE overrides.session_id = segments.session_id
                      AND overrides.segment_id = segments.segment_id
                ),
                role_source = 'user_row'
            WHERE session_id = ?
              AND segment_id IN (
                    SELECT segment_id FROM row_role_overrides
                    WHERE session_id = ?
                )
            """,
            (session_id, session_id),
        )

    def cleanup(self, session_id: str) -> None:
        """Remove a transcript history when the session is no longer needed.

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
            self._conn.execute(
                "DELETE FROM row_role_overrides WHERE session_id = ?", (session_id,)
            )
            self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            self._conn.commit()

    @property
    def session_count(self) -> int:
        """Count transcript histories available for restore.

        Returns:
            Number of stored sessions; `0` means no persisted history exists.
        """
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
        return row[0] if row else 0

    def close(self) -> None:
        """Close SQLite resources when the API process is shutting down."""
        with self._lock:
            self._conn.close()
