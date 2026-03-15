"""
Session lifecycle manager — atomic session registration and cleanup.

Solves two problems:
  1. Race condition: WebSocket disconnect cleanup_session() can delete
     RoleMappingState while /roles/stream is still reading it.
  2. Uncoordinated cleanup: active_sessions, role state, and inference
     queues were cleaned up independently without synchronization.

This class provides a single point of control for session state transitions,
protected by per-session asyncio locks.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from nemo_session import TranscriptionSession

from tools.assign_roles import cleanup_session as cleanup_role_state

logger = logging.getLogger(__name__)


_SESSION_LOCK_TIMEOUT = 5.0
CloseRoleInferenceFn = Callable[[str], Awaitable[None]]


class SessionLifecycle:
    """Coordinates session registration and teardown under per-session locks."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._active: dict[str, TranscriptionSession] = {}
        self._sse_consumers: dict[str, int] = {}

    async def register(self, session_id: str, session: TranscriptionSession) -> None:
        """Register a new active transcription session."""
        lock = self._get_or_create_lock(session_id)
        try:
            await asyncio.wait_for(lock.acquire(), timeout=_SESSION_LOCK_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning("session_lifecycle.register_lock_timeout", extra={
                "session_id": session_id,
            })
            return
        try:
            self._active[session_id] = session
        finally:
            lock.release()

    async def destroy(
        self,
        session_id: str,
        close_role_inference_fn: CloseRoleInferenceFn | None = None,
    ) -> None:
        """Atomically tear down all state for a session under its lock.

        Args:
            session_id: The session to destroy.
            close_role_inference_fn: Async callable to shut down the inference
                worker for this session (injected to avoid circular imports).
        """
        lock = self._get_or_create_lock(session_id)
        try:
            await asyncio.wait_for(lock.acquire(), timeout=_SESSION_LOCK_TIMEOUT)
        except asyncio.TimeoutError:
            logger.error("session_lifecycle.destroy_lock_timeout", extra={
                "session_id": session_id,
            })
            self._active.pop(session_id, None)
            if close_role_inference_fn is not None:
                try:
                    await close_role_inference_fn(session_id)
                except Exception:
                    logger.exception("session_lifecycle.destroy_close_role_inference_failed", extra={
                        "session_id": session_id,
                    })
            if self._sse_consumers.get(session_id, 0) == 0:
                cleanup_role_state(session_id)
                self._locks.pop(session_id, None)
            return
        try:
            self._active.pop(session_id, None)
            if close_role_inference_fn is not None:
                await close_role_inference_fn(session_id)
            # Only clean role state if no SSE consumers are reading it
            if self._sse_consumers.get(session_id, 0) == 0:
                cleanup_role_state(session_id)
        finally:
            lock.release()

        # Clean up the lock itself if no one else needs it
        if self._sse_consumers.get(session_id, 0) == 0:
            self._locks.pop(session_id, None)

    def sse_consumer_start(self, session_id: str) -> None:
        """Increment the SSE consumer count for a session."""
        self._sse_consumers[session_id] = self._sse_consumers.get(session_id, 0) + 1

    def sse_consumer_end(self, session_id: str) -> None:
        """Decrement the SSE consumer count. Clean up role state if session is gone."""
        count = self._sse_consumers.get(session_id, 0) - 1
        if count <= 0:
            self._sse_consumers.pop(session_id, None)
            # If the session was already destroyed while SSE was active, clean up now
            if not self.is_active(session_id):
                cleanup_role_state(session_id)
                self._locks.pop(session_id, None)
        else:
            self._sse_consumers[session_id] = count

    async def get_lock(self, session_id: str) -> asyncio.Lock:
        """Return the per-session lock for safe state reads."""
        return self._get_or_create_lock(session_id)

    def get(self, session_id: str) -> TranscriptionSession | None:
        """Return the active TranscriptionSession or None."""
        return self._active.get(session_id)

    def is_active(self, session_id: str) -> bool:
        """Check whether a session is currently connected."""
        return session_id in self._active

    @property
    def active_count(self) -> int:
        """Number of currently active sessions (for monitoring)."""
        return len(self._active)

    def clear(self) -> None:
        """Clear all state (for testing)."""
        self._active.clear()
        self._locks.clear()
        self._sse_consumers.clear()

    def _get_or_create_lock(self, session_id: str) -> asyncio.Lock:
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        return self._locks[session_id]
