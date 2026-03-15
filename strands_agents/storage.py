"""
Storage backends for transcript session persistence.

=============================================================================
WHAT THIS FILE DOES
=============================================================================

Defines the StorageBackend protocol and concrete implementations:

  - StorageBackend: Protocol that both SessionStore and SqliteBackend satisfy.
  - SqliteBackend: Persistent storage using a single SQLite file.

The in-memory SessionStore (session.py) already conforms to the protocol
without changes.

=============================================================================
SQLITE SCHEMA
=============================================================================

  sessions(id TEXT PK, created_at REAL, last_accessed_at REAL)
  segments(id INTEGER PK, session_id TEXT FK, speaker_id TEXT, text TEXT,
           start REAL, end REAL, is_interim INTEGER, role TEXT, position INTEGER)

Thread safety: check_same_thread=False + threading.Lock for all writes.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import Protocol, runtime_checkable


@runtime_checkable
class StorageBackend(Protocol):
    """Protocol for transcript storage backends.

    Both the in-memory SessionStore and SqliteBackend implement this.
    """

    def append_segment(self, session_id: str, segment: dict) -> None: ...
    def replace_segments(self, session_id: str, segments: list[dict]) -> None: ...
    def get_segments(self, session_id: str) -> list[dict]: ...
    def get_transcript_text(self, session_id: str, max_chars: int = 3500) -> str: ...
    def apply_role_mapping(self, session_id: str, mapping: dict[str, str]) -> None: ...
    def cleanup(self, session_id: str) -> None: ...

    @property
    def session_count(self) -> int: ...


SESSION_DB_PATH = os.environ.get("SESSION_DB_PATH", "/data/sessions.db")


class SqliteBackend:
    """Persistent transcript storage backed by a single SQLite file.

    Thread-safe: uses check_same_thread=False and a threading.Lock for all
    write operations. Read operations do not acquire the lock (SQLite WAL
    mode allows concurrent readers).

    Tables are created on init if they don't exist.
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
        """Create sessions and segments tables if they don't exist."""
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
                    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_segments_session_position
                    ON segments(session_id, position);
            """)

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
        """Append a transcript segment to the session."""
        with self._lock:
            self._ensure_session(session_id)
            position = self._next_position(session_id)
            self._conn.execute(
                """
                INSERT INTO segments (session_id, speaker_id, text, start, end, is_interim, role, position)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
                ),
            )
            self._conn.commit()

    def replace_segments(self, session_id: str, segments: list[dict]) -> None:
        """Replace all segments for a session (transactional)."""
        with self._lock:
            self._ensure_session(session_id)
            self._conn.execute(
                "DELETE FROM segments WHERE session_id = ?",
                (session_id,),
            )
            for position, segment in enumerate(segments):
                self._conn.execute(
                    """
                    INSERT INTO segments (session_id, speaker_id, text, start, end, is_interim, role, position)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
                    ),
                )
            self._conn.commit()

    def get_segments(self, session_id: str) -> list[dict]:
        """Return all segments for a session ordered by position."""
        rows = self._conn.execute(
            """
            SELECT speaker_id, text, start, end, is_interim, role
            FROM segments
            WHERE session_id = ?
            ORDER BY position
            """,
            (session_id,),
        ).fetchall()

        segments = []
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
            segments.append(seg)
        return segments

    def get_transcript_text(self, session_id: str, max_chars: int = 3500) -> str:
        """Return the accumulated transcript as plain text (for role inference context).

        Returns the first 500 chars (opening context) plus the last (max_chars - 500)
        chars (recent context), separated by an ellipsis marker.
        """
        segments = self.get_segments(session_id)
        lines = []
        for seg in segments:
            speaker = seg.get("role", seg.get("speaker_id", "UNKNOWN"))
            text = seg.get("text", "")
            lines.append(f"[{speaker}] {text}")

        full_text = "\n".join(lines)
        if len(full_text) > max_chars:
            first_size = min(500, max_chars // 3)
            last_size = max_chars - first_size
            first = full_text[:first_size]
            last = full_text[-last_size:]
            return first + "\n...\n" + last
        return full_text

    def apply_role_mapping(self, session_id: str, mapping: dict[str, str]) -> None:
        """Update the role column for segments matching speaker_ids in the mapping."""
        with self._lock:
            for speaker_id, role in mapping.items():
                self._conn.execute(
                    """
                    UPDATE segments SET role = ?
                    WHERE session_id = ? AND speaker_id = ?
                    """,
                    (role, session_id, speaker_id),
                )
            self._conn.commit()

    def cleanup(self, session_id: str) -> None:
        """Remove a session and all its segments."""
        with self._lock:
            self._conn.execute("DELETE FROM segments WHERE session_id = ?", (session_id,))
            self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            self._conn.commit()

    @property
    def session_count(self) -> int:
        """Number of sessions in the database."""
        row = self._conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
        return row[0] if row else 0

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
