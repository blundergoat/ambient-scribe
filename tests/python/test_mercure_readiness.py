"""CPU-only contracts for the M05 Mercure readiness probe."""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import httpx
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER_PATH = REPO_ROOT / "scripts/probe-mercure-readiness.py"
HEX_A = "a" * 64
HEX_B = "b" * 64
HEX_C = "c" * 64
HEX_D = "d" * 64


def load_helper() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "m05_mercure_readiness",
        HELPER_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load M05 Mercure readiness helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


def valid_request() -> dict[str, Any]:
    return {
        "schema_version": "ambient-scribe-m05-mercure-readiness-request/v1",
        "mercure_container_id": HEX_A,
        "mercure_image_id": f"sha256:{HEX_B}",
        "nemo_container_id": HEX_C,
        "nemo_image_id": f"sha256:{HEX_D}",
        "mercure_container_running": True,
        "nemo_dns_resolved": True,
        "nonce_sha256": HEX_B,
        "attempt_spent": False,
    }


class FakeAsyncClient:
    """Record the timeout while providing an async context manager."""

    created_timeouts: list[httpx.Timeout] = []

    def __init__(self, *, timeout: httpx.Timeout) -> None:
        self.created_timeouts.append(timeout)

    async def __aenter__(self) -> FakeAsyncClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        return None


def fake_publisher(
    result: object = True,
    *,
    raised_error: Exception | None = None,
) -> SimpleNamespace:
    calls: list[dict[str, Any]] = []
    publisher = SimpleNamespace(MERCURE_PUBLISH_MAX_RETRIES=3, calls=calls)

    async def did_publish_mercure_event(
        topic: str,
        data: dict[str, Any],
        http_client: object,
        event_id: int | None = None,
    ) -> object:
        calls.append(
            {
                "topic": topic,
                "data": data,
                "http_client": http_client,
                "event_id": event_id,
                "retries_during_call": publisher.MERCURE_PUBLISH_MAX_RETRIES,
            }
        )
        if raised_error is not None:
            raise raised_error

        return result

    publisher.did_publish_mercure_event = did_publish_mercure_event

    return publisher


def monotonic_values(*values: float):
    remaining = iter(values)

    return lambda: next(remaining)


def test_success_calls_existing_publisher_once_and_is_deterministic() -> None:
    helper = load_helper()
    publisher = fake_publisher()
    wait_timeouts: list[float] = []

    async def wait_for(awaitable, timeout: float):
        wait_timeouts.append(timeout)

        return await awaitable

    first = asyncio.run(
        helper.build_readiness_receipt(
            valid_request(),
            publisher_module=publisher,
            client_factory=FakeAsyncClient,
            wait_for=wait_for,
            monotonic=monotonic_values(10.0, 10.125),
        )
    )
    second_publisher = fake_publisher()
    second = asyncio.run(
        helper.build_readiness_receipt(
            valid_request(),
            publisher_module=second_publisher,
            client_factory=FakeAsyncClient,
            wait_for=wait_for,
            monotonic=monotonic_values(20.0, 20.125),
        )
    )

    assert helper.render_document(first) == helper.render_document(second)
    assert first == {
        "schema_version": "ambient-scribe-m05-mercure-readiness/v1",
        "status": "ready",
        "mercure_container_id": HEX_A,
        "mercure_image_id": f"sha256:{HEX_B}",
        "nemo_container_id": HEX_C,
        "nemo_image_id": f"sha256:{HEX_D}",
        "mercure_container_running": True,
        "nemo_dns_resolved": True,
        "authenticated_publish_accepted": True,
        "publish_attempts": 1,
        "topic_class": "m05_non_session_readiness",
        "nonce_sha256": HEX_B,
        "duration_ms": 125,
        "timeout_seconds": 5.0,
        "credentials_emitted": False,
        "attempt_spent": False,
    }
    assert len(publisher.calls) == 1
    assert publisher.calls[0]["retries_during_call"] == 1
    assert publisher.calls[0]["topic"] == f"scribe/readiness/m05/{HEX_B}"
    assert "/session/" not in publisher.calls[0]["topic"]
    assert publisher.calls[0]["data"] == {
        "schema_version": "ambient-scribe-m05-mercure-readiness/v1",
        "type": "m05_mercure_readiness",
        "nonce_sha256": HEX_B,
    }
    assert publisher.calls[0]["event_id"] is None
    assert publisher.MERCURE_PUBLISH_MAX_RETRIES == 3
    assert wait_timeouts == [5.0, 5.0]
    assert FakeAsyncClient.created_timeouts[-1].connect == 5.0


@pytest.mark.parametrize("result", [False, None, "true", 1])
def test_rejects_every_non_true_publish_result(result: object) -> None:
    helper = load_helper()
    publisher = fake_publisher(result)

    with pytest.raises(helper.ReadinessProbeError, match="publish_rejected"):
        asyncio.run(
            helper.build_readiness_receipt(
                valid_request(),
                publisher_module=publisher,
                client_factory=FakeAsyncClient,
                monotonic=monotonic_values(10.0, 10.1),
            )
        )

    assert len(publisher.calls) == 1
    assert publisher.MERCURE_PUBLISH_MAX_RETRIES == 3


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (TimeoutError("credential-shaped timeout detail"), "publish_timeout"),
        (RuntimeError("credential-shaped failure detail"), "publish_failed"),
    ],
)
def test_transport_failures_expose_only_safe_error_codes(
    error: Exception,
    code: str,
) -> None:
    helper = load_helper()
    publisher = fake_publisher(raised_error=error)

    with pytest.raises(helper.ReadinessProbeError, match=code) as raised:
        asyncio.run(
            helper.build_readiness_receipt(
                valid_request(),
                publisher_module=publisher,
                client_factory=FakeAsyncClient,
                monotonic=monotonic_values(10.0),
            )
        )

    assert str(raised.value) == code
    assert "credential-shaped" not in str(raised.value)
    assert publisher.MERCURE_PUBLISH_MAX_RETRIES == 3


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("mercure_container_running", False, "mercure_not_running"),
        ("nemo_dns_resolved", False, "nemo_dns_unresolved"),
        ("attempt_spent", True, "attempt_already_spent"),
        ("nonce_sha256", "not-a-hash", "invalid_nonce_sha256"),
        ("mercure_image_id", HEX_B, "invalid_mercure_image_id"),
    ],
)
def test_request_validation_fails_closed(
    field: str,
    value: object,
    code: str,
) -> None:
    helper = load_helper()
    request = valid_request()
    request[field] = value

    with pytest.raises(helper.ReadinessProbeError, match=code):
        helper.validate_request(request)


def test_request_rejects_unknown_sensitive_shaped_field() -> None:
    helper = load_helper()
    request = valid_request()
    request["authorization"] = "Bearer must-never-be-emitted"

    with pytest.raises(helper.ReadinessProbeError, match="invalid_request_fields"):
        helper.validate_request(request)


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("status", "failed", "readiness_not_proven"),
        ("publish_attempts", 2, "invalid_publish_attempts"),
        ("publish_attempts", True, "invalid_publish_attempts"),
        ("duration_ms", 5001, "invalid_duration_ms"),
        ("timeout_seconds", 6.0, "invalid_timeout_seconds"),
        ("credentials_emitted", True, "credentials_emitted"),
        ("attempt_spent", True, "attempt_already_spent"),
    ],
)
def test_receipt_validation_rejects_malformed_values(
    field: str,
    value: object,
    code: str,
) -> None:
    helper = load_helper()
    publisher = fake_publisher()
    receipt = asyncio.run(
        helper.build_readiness_receipt(
            valid_request(),
            publisher_module=publisher,
            client_factory=FakeAsyncClient,
            monotonic=monotonic_values(10.0, 10.1),
        )
    )
    receipt[field] = value

    with pytest.raises(helper.ReadinessProbeError, match=code):
        helper.validate_receipt(receipt)


def test_receipt_rejects_every_extra_field() -> None:
    helper = load_helper()
    publisher = fake_publisher()
    receipt = asyncio.run(
        helper.build_readiness_receipt(
            valid_request(),
            publisher_module=publisher,
            client_factory=FakeAsyncClient,
            monotonic=monotonic_values(10.0, 10.1),
        )
    )
    receipt["exception"] = "must-never-be-emitted"

    with pytest.raises(helper.ReadinessProbeError, match="invalid_receipt_fields"):
        helper.validate_receipt(receipt)


def test_cli_failure_emits_no_receipt_or_sensitive_input() -> None:
    helper = load_helper()
    secret_marker = "must-never-be-emitted"
    request = valid_request()
    request["authorization"] = secret_marker
    stdout = io.StringIO()
    stderr = io.StringIO()

    status = helper.main(
        [],
        stdin=io.StringIO(json.dumps(request)),
        stdout=stdout,
        stderr=stderr,
    )

    assert status == 1
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "m05-mercure-readiness: invalid_request_fields\n"
    assert secret_marker not in stderr.getvalue()


def test_cli_receipt_validation_is_offline_and_canonical() -> None:
    helper = load_helper()
    publisher = fake_publisher()
    receipt = asyncio.run(
        helper.build_readiness_receipt(
            valid_request(),
            publisher_module=publisher,
            client_factory=FakeAsyncClient,
            monotonic=monotonic_values(10.0, 10.1),
        )
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    status = helper.main(
        ["--validate-receipt"],
        stdin=io.StringIO(json.dumps(receipt, indent=2)),
        stdout=stdout,
        stderr=stderr,
    )

    assert status == 0
    assert stdout.getvalue() == helper.render_document(receipt)
    assert stderr.getvalue() == ""


def test_production_retry_default_remains_three() -> None:
    helper = load_helper()
    publisher = helper._load_publisher_module()

    assert publisher.MERCURE_PUBLISH_MAX_RETRIES == 3


def test_rejects_production_retry_drift() -> None:
    helper = load_helper()
    publisher = fake_publisher()
    publisher.MERCURE_PUBLISH_MAX_RETRIES = 2

    with pytest.raises(helper.ReadinessProbeError, match="publisher_seam_invalid"):
        asyncio.run(
            helper.build_readiness_receipt(
                valid_request(),
                publisher_module=publisher,
                client_factory=FakeAsyncClient,
                monotonic=monotonic_values(10.0),
            )
        )

    assert publisher.calls == []
