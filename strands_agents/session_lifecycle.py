"""
Session lifecycle manager — atomic session registration and cleanup.

Provides a single point of control for session state transitions,
protected by per-session asyncio locks. Coordinates cleanup of
active sessions, role state, and inference queues.

Supports a reconnection grace period: when a WebSocket disconnects,
destruction can be scheduled with a delay via schedule_destroy().
If the same session_id reconnects within the grace window, the
pending destroy is cancelled and the existing TranscriptionSession
is preserved (audio buffer + transcript state intact).
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
        self._pending_destroys: dict[str, asyncio.Task[None]] = {}

    async def register(self, session_id: str, session: TranscriptionSession) -> None:
        """Register a new active transcription session.

        If a pending destroy is scheduled for this session_id (from a
        previous disconnect), it is cancelled so the session survives.
        """
        # Cancel any pending graceful destroy — the session is being resumed.
        pending = self._pending_destroys.pop(session_id, None)
        if pending is not None and not pending.done():
            pending.cancel()
            logger.info("session_lifecycle.pending_destroy_cancelled", extra={
                "session_id": session_id,
            })

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
        """Atomically tear down all state for a session under its lock."""
        lock = self._get_or_create_lock(session_id)
        try:
            await asyncio.wait_for(lock.acquire(), timeout=_SESSION_LOCK_TIMEOUT)
        except asyncio.TimeoutError:
            logger.error("session_lifecycle.destroy_lock_timeout", extra={
                "session_id": session_id,
            })
            # Best-effort cleanup without the lock
            self._active.pop(session_id, None)
            if close_role_inference_fn is not None:
                try:
                    await close_role_inference_fn(session_id)
                except Exception:
                    logger.exception("session_lifecycle.destroy_close_role_inference_failed")
            cleanup_role_state(session_id)
            self._locks.pop(session_id, None)
            return
        try:
            self._active.pop(session_id, None)
            if close_role_inference_fn is not None:
                await close_role_inference_fn(session_id)
            cleanup_role_state(session_id)
        finally:
            lock.release()
            self._locks.pop(session_id, None)

    async def schedule_destroy(
        self,
        session_id: str,
        close_role_inference_fn: CloseRoleInferenceFn | None = None,
        grace_seconds: float = 30.0,
    ) -> None:
        """Schedule session destruction after a grace period.

        If the same session_id reconnects before the timer fires,
        register() cancels the pending task and the session survives.

        If already scheduled (e.g. duplicate disconnect), this is a no-op.
        """
        if session_id in self._pending_destroys:
            return  # already scheduled

        async def _delayed_destroy() -> None:
            logger.info("session_lifecycle.grace_period_expired", extra={
                "session_id": session_id,
                "grace_seconds": grace_seconds,
            })
            await self.destroy(session_id, close_role_inference_fn)
            self._pending_destroys.pop(session_id, None)

        task = asyncio.create_task(
            _delayed_destroy(),
            name=f"grace-destroy-{session_id}",
        )
        self._pending_destroys[session_id] = task
        logger.info("session_lifecycle.destroy_scheduled", extra={
            "session_id": session_id,
            "grace_seconds": grace_seconds,
        })

    def has_pending_destroy(self, session_id: str) -> bool:
        """Check whether a graceful destroy is pending for this session."""
        task = self._pending_destroys.get(session_id)
        return task is not None and not task.done()

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
        # Cancel any pending graceful destroys
        for task in self._pending_destroys.values():
            if not task.done():
                task.cancel()
        self._pending_destroys.clear()

    def _get_or_create_lock(self, session_id: str) -> asyncio.Lock:
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        return self._locks[session_id]
