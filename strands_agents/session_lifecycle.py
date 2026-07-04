"""
Lifecycle coordinator for browser recording sessions.

It registers live WebSocket sessions, keeps a short reconnect grace window, and
cleans audio plus role state after the user leaves. This is what lets a browser
refresh resume a transcript briefly instead of losing the current visit.
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
    """
    Coordinates live recording registration and teardown.

    Use it whenever WebSocket, transcript, and role state must move together
    from the user's perspective. Per-session locks prevent reconnect and cleanup
    races from corrupting one visible transcript.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._active: dict[str, TranscriptionSession] = {}
        self._pending_destroys: dict[str, asyncio.Task[None]] = {}

    async def register(self, session_id: str, session: TranscriptionSession) -> None:
        """Register a new active transcription session.

        If a pending destroy is scheduled for this session_id (from a
        previous disconnect), it is cancelled so the session survives.

        Args:
            session_id: Recording UUID used by the browser.
            session: Audio/transcript state to resume or show live.
        """
        # Cancel any pending graceful destroy — the session is being resumed.
        pending = self._pending_destroys.pop(session_id, None)
        if pending is not None and not pending.done():
            pending.cancel()
            logger.info(
                "session_lifecycle.pending_destroy_cancelled",
                extra={
                    "session_id": session_id,
                },
            )

        lock = self._get_or_create_lock(session_id)
        try:
            await asyncio.wait_for(lock.acquire(), timeout=_SESSION_LOCK_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning(
                "session_lifecycle.register_lock_timeout",
                extra={
                    "session_id": session_id,
                },
            )
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
        """Atomically tear down all state after the user leaves a session.

        Args:
            session_id: Recording UUID whose live state should disappear.
            close_role_inference_fn: Optional role-queue closer; `None` means only audio state is cleared.
        """
        lock = self._get_or_create_lock(session_id)
        try:
            await asyncio.wait_for(lock.acquire(), timeout=_SESSION_LOCK_TIMEOUT)
        except asyncio.TimeoutError:
            logger.error(
                "session_lifecycle.destroy_lock_timeout",
                extra={
                    "session_id": session_id,
                },
            )
            # Best-effort cleanup without the lock
            self._active.pop(session_id, None)
            if close_role_inference_fn is not None:
                try:
                    await close_role_inference_fn(session_id)
                except Exception:
                    logger.exception(
                        "session_lifecycle.destroy_close_role_inference_failed"
                    )
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

        Args:
            session_id: Recording UUID that may still reconnect.
            close_role_inference_fn: Optional role cleanup callback; `None` keeps cleanup local to lifecycle state.
            grace_seconds: Seconds the browser can reconnect before cleanup runs.
        """
        # Duplicate disconnects should not shorten the user's reconnect window.
        if session_id in self._pending_destroys:
            return  # already scheduled

        async def _delayed_destroy() -> None:
            try:
                await asyncio.sleep(grace_seconds)
                logger.info(
                    "session_lifecycle.grace_period_expired",
                    extra={
                        "session_id": session_id,
                        "grace_seconds": grace_seconds,
                    },
                )
                await self.destroy(session_id, close_role_inference_fn)
            finally:
                current_task = asyncio.current_task()
                # The delay task owns its pending marker until cleanup finishes.
                if self._pending_destroys.get(session_id) is current_task:
                    self._pending_destroys.pop(session_id, None)

        task = asyncio.create_task(
            _delayed_destroy(),
            name=f"grace-destroy-{session_id}",
        )
        self._pending_destroys[session_id] = task
        logger.info(
            "session_lifecycle.destroy_scheduled",
            extra={
                "session_id": session_id,
                "grace_seconds": grace_seconds,
            },
        )

    def has_pending_destroy(self, session_id: str) -> bool:
        """Check whether a browser can still resume during the grace window.

        Args:
            session_id: Recording UUID to inspect.

        Returns:
            True while cleanup is scheduled but not complete; false means no grace is pending.
        """
        task = self._pending_destroys.get(session_id)
        return task is not None and not task.done()

    def get(self, session_id: str) -> TranscriptionSession | None:
        """Return live audio state for a recording, if the browser can resume it.

        Args:
            session_id: Recording UUID requested by the browser.

        Returns:
            Active session, or `None` when the transcript cannot be resumed.
        """
        return self._active.get(session_id)

    def is_active(self, session_id: str) -> bool:
        """Report whether the browser currently has a live recording socket.

        Args:
            session_id: Recording UUID to inspect.

        Returns:
            True for connected sessions, false for ended or grace-only sessions.
        """
        return session_id in self._active

    @property
    def active_count(self) -> int:
        """Count currently connected browser recordings.

        Returns:
            Number of active WebSocket sessions; `0` means no live recordings.
        """
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
