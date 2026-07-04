"""Tests for local observability logs, metrics extraction, and eval scripts."""

from __future__ import annotations

import importlib.util
import json
import logging
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from logging_config import JsonLoggingFormatter


REPO_ROOT = Path(__file__).resolve().parents[2]


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


def test_agent_metric_fields_extracts_sdk_summary():
    """Strands AgentResult metrics become bounded log fields without model text."""
    from api.server import _agent_metric_fields

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
