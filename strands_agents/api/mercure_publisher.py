"""
Mercure publishing helper for browser transcript events.

FastAPI routes call this when raw transcript text, role labels, or summaries
need to reach the browser through Mercure. Keeping retry and JWT handling here
keeps the route module focused on user workflows instead of transport details.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

import httpx
# Declared in strands_agents/requirements.txt; importing at module load makes a
# broken image fail at startup (caught by the container healthcheck) instead of
# silently skipping every browser-visible publish at runtime.
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

    Returns:
        JWT string; empty means transcript, role, and summary events cannot be published.
    """
    global _mercure_jwt_cache
    # Cached JWT avoids recomputing the token for every visible transcript event.
    if _mercure_jwt_cache is not None:
        return _mercure_jwt_cache

    jwt_env = os.environ.get("MERCURE_JWT", "")
    # A pre-signed token is used as-is when ops supplies one.
    if jwt_env:
        _mercure_jwt_cache = jwt_env
        return _mercure_jwt_cache

    secret = MERCURE_JWT_SECRET
    # No secret means the browser cannot receive live transcript updates.
    if not secret:
        _mercure_jwt_cache = ""
        return _mercure_jwt_cache

    # The documented contract is >= 32 chars for HS256; minting a token from a
    # weaker secret would silently weaken the publish channel.
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
        # Without this log, a malformed secret is later indistinguishable
        # from a deliberately unset MERCURE_JWT_SECRET.
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

    Args:
        topic: Mercure topic for a transcript, role, or summary event; empty prevents routing.
        data: JSON payload shown to the browser; empty still publishes an event shell.
        http_client: Shared FastAPI HTTP client; null is never expected after app startup.
        event_id: Monotonic event ID for reconnect resume; null means Mercure assigns no replay ID.

    Returns:
        True when Mercure accepted the event; false means the browser did not receive this update.
    """
    publish_started_at = time.time()
    session_id = session_id_from_topic(topic)
    token = _resolve_mercure_jwt()
    # Without a publisher token, the browser cannot receive transcript or role events.
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
    # Event IDs let a reconnecting browser resume from the last delivered transcript update.
    if event_id is not None:
        payload["id"] = str(event_id)

    last_error: Exception | None = None
    # Each retry attempts to deliver the same browser-visible Mercure event.
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
            # Intermediate failures are warnings because a later retry may still reach the browser.
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
