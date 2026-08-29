"""
Mercure publishing helper for browser transcript events.

FastAPI routes call this whenever raw transcript text, role labels, or a finished summary needs to reach the clinician's browser.

Everything the clinician watches appear live during a consultation arrives through this one function, so a failure here is
visible as a transcript that stops updating rather than as an error dialog. Retry, backoff, and JWT handling live here to keep
the route module about user workflows instead of transport details.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

import httpx

# Declared in strands_agents/requirements.txt. Importing at module load makes a broken image fail at startup, where the
# container healthcheck catches it, instead of silently skipping every browser-visible publish once clinicians are recording.
import jwt as pyjwt

from api.agent_observability import session_id_from_topic

logger = logging.getLogger(__name__)

MERCURE_HUB_URL = os.environ.get(
    "MERCURE_HUB_URL", "http://mercure:3701/.well-known/mercure"
)
MERCURE_JWT_SECRET = os.environ.get("MERCURE_JWT_SECRET", "")
MERCURE_PUBLISH_MAX_RETRIES = 3
MERCURE_PUBLISH_BACKOFF_SECONDS = 2.0
_mercure_jwt_cache: str | None = None


def _resolve_mercure_jwt() -> str:
    """Return the publisher JWT used to deliver browser-visible Mercure events.

    Resolved once and cached, because every transcript row published during a consultation needs the same token.

    Returns:
        JWT string; empty means transcript, role, and summary events cannot be published, so the browser stays silent.
    """
    global _mercure_jwt_cache
    # A cached token avoids re-signing on every visible transcript event during a long consultation.
    if _mercure_jwt_cache is not None:
        return _mercure_jwt_cache

    jwt_env = os.environ.get("MERCURE_JWT", "")
    # A pre-signed token is used as-is when ops supplies one.
    if jwt_env:
        _mercure_jwt_cache = jwt_env
        return _mercure_jwt_cache

    secret = MERCURE_JWT_SECRET
    # No secret means the browser cannot receive live transcript updates at all, so publishing is disabled rather than attempted.
    if not secret:
        _mercure_jwt_cache = ""
        return _mercure_jwt_cache

    # The documented contract is at least 32 characters for HS256; minting a token from a weaker secret would quietly weaken the channel.
    if len(secret) < 32:
        logger.error(
            "mercure.jwt_secret_too_short length=%s required=32; publishes disabled",
            len(secret),
        )
        _mercure_jwt_cache = ""
        return _mercure_jwt_cache

    try:
        token = pyjwt.encode(
            {"mercure": {"publish": ["*"]}},
            secret,
            algorithm="HS256",
        )
        _mercure_jwt_cache = token if isinstance(token, str) else token.decode("utf-8")
    except Exception:
        # Example: MERCURE_JWT_SECRET is set to a value PyJWT cannot sign with, so no clinician sees live transcript text this run.
        # The log is what separates that from a deliberately unset secret, which fails the same way but is an intentional configuration.
        logger.exception("mercure.jwt_encode_failed; publishes disabled")
        _mercure_jwt_cache = ""

    return _mercure_jwt_cache


async def did_publish_mercure_event(
    topic: str,
    data: dict[str, Any],
    http_client: httpx.AsyncClient,
    event_id: int | None = None,
) -> bool:
    """Publish one browser-visible Mercure event with bounded retry.

    Use for every update the clinician should see mid-consultation: a new transcript row, a role label change, or a finished note.

    Args:
        topic: Mercure topic for a transcript, role, or summary event; empty prevents routing.
        data: JSON payload shown to the browser; empty still publishes an event shell.
        http_client: Shared FastAPI HTTP client; null is never expected after app startup.
        event_id: Monotonic event ID for reconnect resume; null means Mercure assigns no replay ID, so a reconnecting browser
            cannot resume from this event and will only see updates published after it reconnects.

    Returns:
        True when Mercure accepted the event; false means the browser did not receive this update and the caller decides
        whether that is worth surfacing to the clinician.
    """
    publish_started_at = time.time()
    session_id = session_id_from_topic(topic)
    token = _resolve_mercure_jwt()
    # Without a publisher token the browser cannot receive transcript or role events, so the attempt is skipped and logged loudly.
    if token == "":
        logger.error(
            "mercure.publish.skipped session_id=%s reason=%s topic=%s",
            session_id,
            "no JWT configured",
            topic,
            extra={
                "session_id": session_id,
                "topic": topic,
                "reason": "no JWT configured",
            },
        )
        return False

    payload: dict[str, str] = {
        "topic": topic,
        "data": json.dumps(data),
    }
    # Event IDs let a browser that dropped its connection resume from the last transcript row it actually rendered.
    if event_id is not None:
        payload["id"] = str(event_id)

    last_error: Exception | None = None
    # Each attempt re-sends the same event, so a brief hub restart costs the clinician a pause rather than a missing row.
    for attempt in range(MERCURE_PUBLISH_MAX_RETRIES):
        try:
            response = await http_client.post(
                MERCURE_HUB_URL,
                data=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            logger.info(
                "mercure.publish.succeeded",
                extra={
                    "session_id": session_id,
                    "topic": topic,
                    "attempts": attempt + 1,
                    "duration_ms": int((time.time() - publish_started_at) * 1000),
                },
            )
            return True
        except Exception as error:
            last_error = error
            # Example: the Mercure container restarts mid-consultation, so this row's POST is refused while the clinician keeps talking.
            # Intermediate failures stay warnings because a later retry may still deliver the row before anyone notices a gap.
            if attempt < MERCURE_PUBLISH_MAX_RETRIES - 1:
                backoff = MERCURE_PUBLISH_BACKOFF_SECONDS * (2**attempt)
                logger.warning(
                    "mercure.publish.retrying session_id=%s attempt=%s %s: %s",
                    session_id,
                    attempt + 1,
                    type(error).__name__,
                    str(error)[:200],
                    exc_info=error,
                    extra={
                        "session_id": session_id,
                        "topic": topic,
                        "attempt": attempt + 1,
                        "backoff_seconds": backoff,
                        "error_type": type(error).__name__,
                        "error": str(error)[:200],
                    },
                )
                await asyncio.sleep(backoff)

    last_error_type = type(last_error).__name__ if last_error is not None else "None"
    last_error_text = str(last_error)[:200] if last_error is not None else "no error"
    # Every retry is spent, so this row never reaches the browser and the clinician's transcript is now missing it permanently.
    logger.error(
        "mercure.publish.failed session_id=%s attempts=%s %s: %s",
        session_id,
        MERCURE_PUBLISH_MAX_RETRIES,
        last_error_type,
        last_error_text,
        exc_info=last_error,
        extra={
            "session_id": session_id,
            "topic": topic,
            "attempts": MERCURE_PUBLISH_MAX_RETRIES,
            "duration_ms": int((time.time() - publish_started_at) * 1000),
            "error_type": last_error_type,
            "error": last_error_text,
        },
    )
    return False
