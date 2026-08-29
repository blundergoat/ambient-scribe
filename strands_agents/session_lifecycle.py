"""
Lifecycle coordinator for browser recording sessions.

Registers live WebSocket sessions, holds a short reconnect grace window, and clears audio plus role state once the
clinician has really gone.

The grace window is the whole point: a browser refresh or a dropped Wi-Fi connection mid-consultation resumes the same
transcript instead of losing the visit. Per-session locks keep a reconnect and a cleanup from racing over one transcript.
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

    Use whenever WebSocket, transcript, and role state must move together from the clinician's point of view: they think
    of one recording, so these three must never disagree about whether that recording still exists.

    Per-session locks prevent a reconnect and a cleanup from corrupting one visible transcript between them.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._active: dict[str, TranscriptionSession] = {}
        self._pending_destroys: dict[str, asyncio.Task[None]] = {}

    async def register(self, session_id: str, session: TranscriptionSession) -> None:
        """Register a new active transcription session.

        Called when the browser opens its recording socket. A destroy already scheduled from a previous disconnect is
        cancelled here, which is what makes a mid-visit refresh resume rather than restart.

        Args:
            session_id: Recording UUID used by the browser.
            session: Audio/transcript state to resume or show live.
        """
        # A pending destroy means the clinician disconnected moments ago and has now come back, so the visit is resumed.
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
        except asyncio.TimeoutError as error:
            # Example: the clinician's browser reconnects while a teardown for the same visit is still holding the lock.
            # Registration is abandoned rather than forced, so the browser retries instead of racing a half-torn-down session.
            logger.warning(
                "session_lifecycle.register_lock_timeout session_id=%s %s",
                session_id,
                type(error).__name__,
                exc_info=error,
                extra={
                    "session_id": session_id,
                    "error_type": type(error).__name__,
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

        Runs once the grace window expires, so the audio buffer, role state, and queue for that visit disappear together.

        Args:
            session_id: Recording UUID whose live state should disappear.
            close_role_inference_fn: Optional role-queue closer; `None` skips draining the queue, and role state is still cleared.
        """
        lock = self._get_or_create_lock(session_id)
        try:
            await asyncio.wait_for(lock.acquire(), timeout=_SESSION_LOCK_TIMEOUT)
        except asyncio.TimeoutError as error:
            # Something else has held this visit's lock for longer than the timeout, most likely another teardown of the
            # same visit still awaiting the role-queue closer, which holds the lock across that await.
            #
            # Cleanup then proceeds without the lock, because leaking a finished visit's audio buffer is the worse outcome.
            logger.error(
                "session_lifecycle.destroy_lock_timeout session_id=%s %s",
                session_id,
                type(error).__name__,
                exc_info=error,
                extra={
                    "session_id": session_id,
                    "error_type": type(error).__name__,
                },
            )
            self._active.pop(session_id, None)
            # A null closer means there is no role queue to drain, so teardown goes straight to clearing role state below.
            if close_role_inference_fn is not None:
                try:
                    await close_role_inference_fn(session_id)
                except Exception as close_error:
                    # The closer already absorbs its own timeout, so anything arriving here is unexpected rather than routine.
                    #
                    # It is logged and swallowed regardless, because the role-state cleanup below is what stops a finished
                    # visit from leaving labels behind, and it must run even when the queue close went wrong.
                    logger.exception(
                        (
                            "session_lifecycle.destroy_close_role_inference_failed "
                            "session_id=%s %s: %s"
                        ),
                        session_id,
                        type(close_error).__name__,
                        str(close_error)[:200],
                        extra={
                            "session_id": session_id,
                            "error_type": type(close_error).__name__,
                            "error": str(close_error)[:200],
                        },
                    )
            cleanup_role_state(session_id)
            self._locks.pop(session_id, None)
            return
        try:
            self._active.pop(session_id, None)
            # A null closer means there is no role queue to drain, so teardown goes straight to clearing role state below.
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

        Called on disconnect. If the same recording reconnects before the timer fires, `register` cancels this task and
        the clinician's transcript survives; a duplicate disconnect is a no-op rather than a second, shorter timer.

        Args:
            session_id: Recording UUID that may still reconnect.
            close_role_inference_fn: Optional role-queue closer passed through to `destroy`; `None` skips draining the queue.
            grace_seconds: Seconds the browser can reconnect before cleanup runs.
        """
        # Duplicate disconnects must not shorten the clinician's reconnect window, so the first timer keeps ownership.
        if session_id in self._pending_destroys:
            return

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
                # Only the task that owns this pending marker may clear it, so a newer timer is never cancelled by an older one.
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
            Active session, or `None` when the transcript cannot be resumed, which the caller treats as an unknown recording.
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
        """Drop every tracked recording and cancel pending teardowns.

        Test-only reset. In a running service, sessions leave one at a time through `destroy`, so calling this mid-visit
        would strand a clinician's live socket with no lifecycle state behind it.
        """
        self._active.clear()
        self._locks.clear()
        # Pending timers hold a reference to this coordinator, so each is cancelled rather than left to fire after the reset.
        for task in self._pending_destroys.values():
            if not task.done():
                task.cancel()
        self._pending_destroys.clear()

    def _get_or_create_lock(self, session_id: str) -> asyncio.Lock:
        """Return the lock guarding one recording, creating it on first use.

        Every visit gets its own lock, so one clinician's teardown never blocks another clinician's reconnect.

        Args:
            session_id: Recording UUID whose lock is needed.

        Returns:
            Lock for this recording; a fresh unlocked one when this is the visit's first register or destroy.
        """
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        return self._locks[session_id]
