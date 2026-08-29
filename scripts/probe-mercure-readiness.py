#!/usr/bin/env python3
"""Run one bounded, secret-free Mercure readiness publish for a baseline campaign.

The future campaign pipes this source into the existing NeMo container. The
probe imports the application's Mercure publisher, limits that isolated Python
process to one publish attempt, and emits only an allowlisted readiness receipt.
It does not inspect environment values or expose transport exceptions.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Callable, Mapping
import importlib
import json
import logging
from pathlib import Path
import re
import sys
import time
from types import ModuleType
from typing import Any, Protocol, TextIO

import httpx


REQUEST_SCHEMA = "ambient-scribe-m05-mercure-readiness-request/v1"
RECEIPT_SCHEMA = "ambient-scribe-m05-mercure-readiness/v1"
TOPIC_CLASS = "m05_non_session_readiness"
TOPIC_PREFIX = "scribe/readiness/m05"
PUBLISH_TIMEOUT_SECONDS = 5.0
PRODUCTION_PUBLISH_ATTEMPTS = 3
MAX_DOCUMENT_BYTES = 16_384

_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_CONTAINER_ID = re.compile(r"^[0-9a-f]{12,64}$")
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_REQUEST_KEYS = frozenset(
    {
        "schema_version",
        "mercure_container_id",
        "mercure_image_id",
        "nemo_container_id",
        "nemo_image_id",
        "mercure_container_running",
        "nemo_dns_resolved",
        "nonce_sha256",
        "attempt_spent",
    }
)
_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "status",
        "mercure_container_id",
        "mercure_image_id",
        "nemo_container_id",
        "nemo_image_id",
        "mercure_container_running",
        "nemo_dns_resolved",
        "authenticated_publish_accepted",
        "publish_attempts",
        "topic_class",
        "nonce_sha256",
        "duration_ms",
        "timeout_seconds",
        "credentials_emitted",
        "attempt_spent",
    }
)


class ReadinessProbeError(ValueError):
    """A fail-closed readiness error represented by a non-sensitive code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class AsyncHttpClient(Protocol):
    """The subset of ``httpx.AsyncClient`` used by the existing publisher."""

    async def __aenter__(self) -> Any: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> bool | None: ...


ClientFactory = Callable[..., AsyncHttpClient]
WaitFor = Callable[[Awaitable[bool], float], Awaitable[bool]]
Monotonic = Callable[[], float]


def _object(value: object, code: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReadinessProbeError(code)

    return value


def _safe_identifier(
    value: object,
    pattern: re.Pattern[str],
    code: str,
) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ReadinessProbeError(code)

    return value


def _exact_bool(value: object, expected: bool, code: str) -> bool:
    if value is not expected:
        raise ReadinessProbeError(code)

    return expected


def validate_request(value: object) -> dict[str, Any]:
    """Validate the exact non-secret request accepted by the runtime probe."""
    request = _object(value, "invalid_request")
    if frozenset(request) != _REQUEST_KEYS:
        raise ReadinessProbeError("invalid_request_fields")
    if request["schema_version"] != REQUEST_SCHEMA:
        raise ReadinessProbeError("invalid_request_schema")

    return {
        "schema_version": REQUEST_SCHEMA,
        "mercure_container_id": _safe_identifier(
            request["mercure_container_id"],
            _CONTAINER_ID,
            "invalid_mercure_container_id",
        ),
        "mercure_image_id": _safe_identifier(
            request["mercure_image_id"],
            _IMAGE_ID,
            "invalid_mercure_image_id",
        ),
        "nemo_container_id": _safe_identifier(
            request["nemo_container_id"],
            _CONTAINER_ID,
            "invalid_nemo_container_id",
        ),
        "nemo_image_id": _safe_identifier(
            request["nemo_image_id"],
            _IMAGE_ID,
            "invalid_nemo_image_id",
        ),
        "mercure_container_running": _exact_bool(
            request["mercure_container_running"],
            True,
            "mercure_not_running",
        ),
        "nemo_dns_resolved": _exact_bool(
            request["nemo_dns_resolved"],
            True,
            "nemo_dns_unresolved",
        ),
        "nonce_sha256": _safe_identifier(
            request["nonce_sha256"],
            _HEX_64,
            "invalid_nonce_sha256",
        ),
        "attempt_spent": _exact_bool(
            request["attempt_spent"],
            False,
            "attempt_already_spent",
        ),
    }



def validate_receipt(value: object) -> dict[str, Any]:
    """Validate and normalize the complete readiness receipt allowlist."""
    receipt = _object(value, "invalid_receipt")
    if frozenset(receipt) != _RECEIPT_KEYS:
        raise ReadinessProbeError("invalid_receipt_fields")
    if receipt["schema_version"] != RECEIPT_SCHEMA:
        raise ReadinessProbeError("invalid_receipt_schema")
    if receipt["status"] != "ready":
        raise ReadinessProbeError("readiness_not_proven")

    duration_ms = receipt["duration_ms"]
    if (
        isinstance(duration_ms, bool)
        or not isinstance(duration_ms, int)
        or not 0 <= duration_ms <= int(PUBLISH_TIMEOUT_SECONDS * 1000)
    ):
        raise ReadinessProbeError("invalid_duration_ms")
    timeout_seconds = receipt["timeout_seconds"]
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or float(timeout_seconds) != PUBLISH_TIMEOUT_SECONDS
    ):
        raise ReadinessProbeError("invalid_timeout_seconds")
    publish_attempts = receipt["publish_attempts"]
    if (
        isinstance(publish_attempts, bool)
        or not isinstance(publish_attempts, int)
        or publish_attempts != 1
    ):
        raise ReadinessProbeError("invalid_publish_attempts")
    if receipt["topic_class"] != TOPIC_CLASS:
        raise ReadinessProbeError("invalid_topic_class")

    return {
        "schema_version": RECEIPT_SCHEMA,
        "status": "ready",
        "mercure_container_id": _safe_identifier(
            receipt["mercure_container_id"],
            _CONTAINER_ID,
            "invalid_mercure_container_id",
        ),
        "mercure_image_id": _safe_identifier(
            receipt["mercure_image_id"],
            _IMAGE_ID,
            "invalid_mercure_image_id",
        ),
        "nemo_container_id": _safe_identifier(
            receipt["nemo_container_id"],
            _CONTAINER_ID,
            "invalid_nemo_container_id",
        ),
        "nemo_image_id": _safe_identifier(
            receipt["nemo_image_id"],
            _IMAGE_ID,
            "invalid_nemo_image_id",
        ),
        "mercure_container_running": _exact_bool(
            receipt["mercure_container_running"],
            True,
            "mercure_not_running",
        ),
        "nemo_dns_resolved": _exact_bool(
            receipt["nemo_dns_resolved"],
            True,
            "nemo_dns_unresolved",
        ),
        "authenticated_publish_accepted": _exact_bool(
            receipt["authenticated_publish_accepted"],
            True,
            "publish_not_accepted",
        ),
        "publish_attempts": 1,
        "topic_class": TOPIC_CLASS,
        "nonce_sha256": _safe_identifier(
            receipt["nonce_sha256"],
            _HEX_64,
            "invalid_nonce_sha256",
        ),
        "duration_ms": duration_ms,
        "timeout_seconds": PUBLISH_TIMEOUT_SECONDS,
        "credentials_emitted": _exact_bool(
            receipt["credentials_emitted"],
            False,
            "credentials_emitted",
        ),
        "attempt_spent": _exact_bool(
            receipt["attempt_spent"],
            False,
            "attempt_already_spent",
        ),
    }



def render_document(document: Mapping[str, Any]) -> str:
    """Render byte-deterministic JSON with no formatting-dependent fields."""
    return json.dumps(
        document,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ) + "\n"


def read_document(stream: TextIO) -> dict[str, Any]:
    """Read one size-bounded JSON object without echoing invalid content."""
    payload = stream.read(MAX_DOCUMENT_BYTES + 1)
    if len(payload.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise ReadinessProbeError("document_too_large")
    try:
        value = json.loads(payload)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ReadinessProbeError("invalid_json") from error

    return _object(value, "invalid_document")


def _load_publisher_module() -> ModuleType:
    repo_agents = Path(__file__).resolve().parents[1] / "strands_agents"
    if repo_agents.is_dir() and str(repo_agents) not in sys.path:
        sys.path.insert(0, str(repo_agents))

    return importlib.import_module("api.mercure_publisher")


async def build_readiness_receipt(
    request_value: object,
    *,
    publisher_module: ModuleType | object | None = None,
    client_factory: ClientFactory = httpx.AsyncClient,
    wait_for: WaitFor = asyncio.wait_for,
    monotonic: Monotonic = time.monotonic,
) -> dict[str, Any]:
    """Publish one readiness event and return a validated secret-free receipt."""
    request = validate_request(request_value)
    publisher = publisher_module or _load_publisher_module()
    publish = getattr(publisher, "did_publish_mercure_event", None)
    original_retries = getattr(publisher, "MERCURE_PUBLISH_MAX_RETRIES", None)
    if (
        not callable(publish)
        or isinstance(original_retries, bool)
        or not isinstance(original_retries, int)
        or original_retries != PRODUCTION_PUBLISH_ATTEMPTS
    ):
        raise ReadinessProbeError("publisher_seam_invalid")

    topic = f"{TOPIC_PREFIX}/{request['nonce_sha256']}"
    payload = {
        "schema_version": RECEIPT_SCHEMA,
        "type": "m05_mercure_readiness",
        "nonce_sha256": request["nonce_sha256"],
    }
    logging_threshold = logging.root.manager.disable
    started_at = monotonic()
    try:
        setattr(publisher, "MERCURE_PUBLISH_MAX_RETRIES", 1)
        logging.disable(logging.CRITICAL)
        async with client_factory(timeout=httpx.Timeout(PUBLISH_TIMEOUT_SECONDS)) as client:
            accepted = await wait_for(
                publish(topic, payload, client, event_id=None),
                PUBLISH_TIMEOUT_SECONDS,
            )
    except TimeoutError as error:
        raise ReadinessProbeError("publish_timeout") from error
    except Exception as error:
        raise ReadinessProbeError("publish_failed") from error
    finally:
        setattr(publisher, "MERCURE_PUBLISH_MAX_RETRIES", original_retries)
        logging.disable(logging_threshold)

    if accepted is not True:
        raise ReadinessProbeError("publish_rejected")
    duration_ms = int(round((monotonic() - started_at) * 1000))
    if not 0 <= duration_ms <= int(PUBLISH_TIMEOUT_SECONDS * 1000):
        raise ReadinessProbeError("publish_duration_out_of_bounds")

    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "status": "ready",
        "mercure_container_id": request["mercure_container_id"],
        "mercure_image_id": request["mercure_image_id"],
        "nemo_container_id": request["nemo_container_id"],
        "nemo_image_id": request["nemo_image_id"],
        "mercure_container_running": True,
        "nemo_dns_resolved": True,
        "authenticated_publish_accepted": True,
        "publish_attempts": 1,
        "topic_class": TOPIC_CLASS,
        "nonce_sha256": request["nonce_sha256"],
        "duration_ms": duration_ms,
        "timeout_seconds": PUBLISH_TIMEOUT_SECONDS,
        "credentials_emitted": False,
        "attempt_spent": False,
    }

    return validate_receipt(receipt)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run or validate the Mercure readiness probe."
    )
    parser.add_argument(
        "--validate-receipt",
        action="store_true",
        help="validate and canonically render a receipt from stdin without publishing",
    )

    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    *,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """Run the probe or the offline receipt validator."""
    try:
        args = parse_args(argv)
        document = read_document(stdin)
        if args.validate_receipt:
            receipt = validate_receipt(document)
        else:
            receipt = asyncio.run(build_readiness_receipt(document))
        stdout.write(render_document(receipt))
    except ReadinessProbeError as error:
        stderr.write(f"m05-mercure-readiness: {error.code}\n")
        return 1
    except Exception:
        stderr.write("m05-mercure-readiness: internal_error\n")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
