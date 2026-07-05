"""Tests for local observability logs, metrics extraction, and eval scripts."""

from __future__ import annotations

import ast
import importlib.util
import json
import logging
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from logging_config import JsonLoggingFormatter


REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHON_SOURCE_ROOT = REPO_ROOT / "strands_agents"


class FakeAgentMetrics:
    """Minimal SDK metrics double for browser-visible role/summary logs."""

    def get_summary(self) -> dict:
        """Return the metric shape exposed by Strands `EventLoopMetrics`.

        Returns:
            Metric summary with token, latency, cycle, and tool-success values.
        """
        return {
            "total_cycles": 2,
            "accumulated_usage": {
                "inputTokens": 11,
                "outputTokens": 7,
                "totalTokens": 18,
            },
            "accumulated_metrics": {"latencyMs": 123},
            "tool_usage": {
                "assign_roles": {
                    "execution_stats": {
                        "success_rate": 1.0,
                    },
                },
            },
        }


class FakeAgentResult:
    """Agent result double whose text is irrelevant but whose metrics are real-shaped."""

    metrics = FakeAgentMetrics()


def load_script(path: Path, module_name: str):
    """Load a hyphenated script file as a Python module for focused tests.

    Args:
        path: Script file to load.
        module_name: Synthetic module name for this test import.

    Returns:
        Imported module object.
    """
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Dataclasses resolve postponed annotations through sys.modules during script import.
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_json_logging_formatter_hoists_join_keys():
    """Formatter emits top-level join IDs and metric fields for the analyzer."""
    record = logging.LogRecord(
        name="api.server",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="role_inference.completed",
        args=(),
        exc_info=None,
    )
    record.session_id = "session-123"
    record.correlation_id = "corr-123"
    record.tokens_total = 18

    parsed = json.loads(JsonLoggingFormatter().format(record))

    assert parsed["event"] == "role_inference.completed"
    assert parsed["session_id"] == "session-123"
    assert parsed["correlation_id"] == "corr-123"
    assert parsed["tokens_total"] == 18


def test_json_logging_formatter_includes_traceback():
    """JSON-default Docker logs keep the stack trace needed after a failed session."""
    try:
        raise ValueError("diagnostic boom")
    except ValueError:
        record = logging.LogRecord(
            name="api.streaming_session",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="websocket.error session_id=%s %s: %s",
            args=("session-123", "ValueError", "diagnostic boom"),
            exc_info=sys.exc_info(),
        )

    parsed = json.loads(JsonLoggingFormatter().format(record))

    assert parsed["error_type"] == "ValueError"
    assert "Traceback (most recent call last)" in parsed["traceback"]
    assert "diagnostic boom" in parsed["traceback"]


def test_agent_metric_fields_extracts_sdk_summary():
    """Strands AgentResult metrics become bounded log fields without model text."""
    from api.agent_observability import agent_metric_fields as _agent_metric_fields

    fields = _agent_metric_fields(FakeAgentResult(), "role-inference")

    assert fields == {
        "agent": "role-inference",
        "tokens_in": 11,
        "tokens_out": 7,
        "tokens_total": 18,
        "model_latency_ms": 123,
        "cycles": 2,
        "tool_success_rate": 1.0,
    }


def test_analyze_logs_report_accepts_json_lines():
    """Analyzer reports latency, token, path, and reliability fields from JSON logs."""
    module = load_script(REPO_ROOT / "scripts/analyze-logs.py", "analyze_logs_script")
    report = "\n".join(
        module.build_report(
            [
                {
                    "event": "role_inference.completed",
                    "session_id": "session-1",
                    "duration_ms": 50,
                    "model_latency_ms": 40,
                    "tokens_total": 18,
                    "tokens_in": 11,
                    "tokens_out": 7,
                    "cycles": 2,
                    "path": "tool",
                    "confidence": 0.9,
                    "tool_success_rate": 1.0,
                },
                {
                    "event": "mercure.publish.succeeded",
                    "session_id": "session-1",
                },
                {
                    "event": "strands.client.call",
                    "session_id": "session-1",
                    "status": 200,
                },
            ]
        )
    )

    assert "tokens_total=18" in report
    assert "paths={'tool': 1}" in report
    assert "mercure={'succeeded': 1}" in report
    assert "php_statuses={'200': 1}" in report


def test_eval_role_heuristic_script_runs_without_gpu():
    """Heuristic eval exits cleanly and prints an overall accuracy report."""
    result = subprocess.run(
        [sys.executable, "scripts/eval-role-heuristic.py"],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    assert "Role heuristic evaluation" in result.stdout
    assert "overall_accuracy=" in result.stdout


def test_session_history_echoes_correlation_id(caplog):
    """PHP-proxied history calls keep the same correlation ID in response and logs."""
    from api.server import app

    session_id = "22222222-2222-4222-8222-222222222222"
    correlation_id = "corr-history"
    client = TestClient(app, raise_server_exceptions=False)

    with caplog.at_level(logging.INFO):
        response = client.post(
            f"/session/{session_id}/history",
            headers={"X-Correlation-ID": correlation_id},
            json={},
        )

    history_logs = [
        record
        for record in caplog.records
        if record.getMessage() == "session_history.completed"
    ]
    assert response.headers["X-Correlation-ID"] == correlation_id
    assert history_logs[-1].session_id == session_id
    assert history_logs[-1].correlation_id == correlation_id


@pytest.mark.asyncio
async def test_streaming_error_log_self_explains(caplog):
    """A mid-stream exception is diagnosable from the plain log line plus traceback."""
    from api.streaming_session import _publish_transcription_error

    session_id = "session-error-shape"
    published_events = []

    async def fake_publish(topic, data, event_id=None):
        published_events.append((topic, data, event_id))
        return True

    services = SimpleNamespace(
        mercure_event_ids={},
        publish_to_mercure=fake_publish,
    )

    with caplog.at_level(logging.ERROR):
        try:
            raise ValueError("buffer size must be a multiple of element size")
        except ValueError as error:
            await _publish_transcription_error(session_id, services, error)

    error_logs = [
        record
        for record in caplog.records
        if record.getMessage().startswith("websocket.error")
    ]

    assert error_logs
    message = error_logs[-1].getMessage()
    assert f"session_id={session_id}" in message
    assert "ValueError" in message
    assert "buffer size must be a multiple of element size" in message
    assert error_logs[-1].exc_info is not None
    assert error_logs[-1].exc_info[0] is ValueError
    assert error_logs[-1].exc_info[2] is not None
    assert published_events[-1][1] == {
        "type": "error",
        "message": "Transcription error occurred",
    }


def test_logger_error_calls_have_plain_diagnostic_fields():
    """Guard against `logger.error("event", extra={...})` hiding failure details."""
    failures: list[str] = []
    for source_path in sorted(PYTHON_SOURCE_ROOT.rglob("*.py")):
        if ".venv" in source_path.parts:
            continue
        tree = ast.parse(source_path.read_text(), filename=str(source_path))
        for node in ast.walk(tree):
            if not _is_logger_level_call(node, "error"):
                continue

            message = _literal_log_message(node)
            has_percent_placeholder = message is not None and "%" in message
            has_exc_info = any(keyword.arg == "exc_info" for keyword in node.keywords)
            if not has_percent_placeholder and not has_exc_info:
                failures.append(f"{source_path.relative_to(REPO_ROOT)}:{node.lineno}")

    assert failures == []


def _is_logger_level_call(node: ast.AST, level: str) -> bool:
    """Return whether an AST call is `logger.<level>(...)`."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == level
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "logger"
    )


def _literal_log_message(node: ast.Call) -> str | None:
    """Return the literal first log argument, when static analysis can see it."""
    if node.args and isinstance(node.args[0], ast.Constant):
        message = node.args[0].value
        if isinstance(message, str):
            return message
    return None
