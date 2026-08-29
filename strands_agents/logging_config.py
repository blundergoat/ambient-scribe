"""
Logging setup for the Python agent service.

Gives local runs and Docker logs the same JSON-line shape the Symfony side emits, so one browser session can be followed end to end.

Set `LOG_FORMAT=json` when `analyze-logs.py` needs to join FastAPI, Mercure, role, and summary events for a single recording.
Console output stays the default, so existing tests and local debugging remain readable without any configuration.
"""

from __future__ import annotations

import json
import logging
import logging.config
import os
import traceback
from datetime import UTC, datetime
from typing import Any

_CONFIGURED = False

_STANDARD_LOG_FIELDS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "taskName",
    "thread",
    "threadName",
}


class JsonLoggingFormatter(logging.Formatter):
    """
    Formats Python log records as canonical Ambient Scribe JSON lines.

    `session_id` and `correlation_id` are hoisted to top-level fields so the log analysis script can join browser, PHP,
    FastAPI, role, and Mercure events for one clinician's recording instead of reading five unrelated streams.

    Use this formatter only for structured process logs. Transcript text must never be passed through it.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Return one JSON object for a browser-visible process event.

        Called by Python logging for every record once `LOG_FORMAT=json` is set.

        Args:
            record: Python log record; missing IDs mean the event is process-scoped, not session-scoped.

        Returns:
            JSON string with stable top-level fields and sanitized extra attributes.
        """
        event: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "level": record.levelname.lower(),
            "event": record.getMessage(),
            "logger": record.name,
        }

        extra_fields = self._extra_fields(record)
        # Correlation IDs are absent during startup, before any browser action exists to correlate against.
        if "correlation_id" in extra_fields:
            event["correlation_id"] = extra_fields.pop("correlation_id")
        # Session IDs appear only once the clinician has started or resumed a recording.
        if "session_id" in extra_fields:
            event["session_id"] = extra_fields.pop("session_id")

        event.update(extra_fields)

        if record.exc_info:
            # JSON is the developer default, so it must carry the same traceback a plain console log would have shown.
            if "error_type" not in event:
                event["error_type"] = record.exc_info[0].__name__
            event["traceback"] = "".join(traceback.format_exception(*record.exc_info))

        return json.dumps(event, ensure_ascii=False, default=_json_default)

    def _extra_fields(self, record: logging.LogRecord) -> dict[str, Any]:
        """Collect non-standard fields supplied through `logger.*(..., extra=...)`.

        These are the fields a caller deliberately attached to explain one step of a clinician's session.

        Args:
            record: Python log record for the current service event.

        Returns:
            Extra attributes safe for JSON serialization; empty means no process context was supplied.
        """
        fields: dict[str, Any] = {}
        # Each non-standard attribute is a field the caller explicitly wanted in the process report.
        for key, value in record.__dict__.items():
            # Standard logging internals would make the JSON line noisy and hard to diff between runs.
            if key in _STANDARD_LOG_FIELDS or key.startswith("_"):
                continue
            fields[key] = _sanitize_log_value(value)
        return fields


def configure_logging() -> None:
    """Install the process logger once for FastAPI, workers, and scripts.

    Use `LOG_FORMAT=json` for machine-readable local logs; otherwise console output stays concise.
    The root handler follows `LOG_LEVEL`, so INFO process events are captured in Docker logs by default.
    """
    global _CONFIGURED

    # Import-time setup must be idempotent, because tests can import the API module more than once in one process.
    if _CONFIGURED:
        return

    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    log_format = os.environ.get("LOG_FORMAT", "console").lower()
    formatter_name = "json" if log_format == "json" else "console"

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "console": {
                    "format": "%(levelname)s:%(name)s:%(message)s",
                },
                "json": {
                    "()": JsonLoggingFormatter,
                },
            },
            "handlers": {
                "default": {
                    "class": "logging.StreamHandler",
                    "formatter": formatter_name,
                },
            },
            "root": {
                "handlers": ["default"],
                "level": log_level,
            },
        }
    )
    _CONFIGURED = True


def _sanitize_log_value(value: Any) -> Any:
    """Convert logging extras into bounded JSON-compatible values.

    Keeps one oversized or unserializable extra from turning a routine session log line into a wall of text.

    Args:
        value: Extra field value supplied by app code; `None` means the field is absent to the user.

    Returns:
        JSON-ready value; unsupported objects become class names instead of raw representations.
    """
    # Plain scalars are already safe to render into a process-quality report.
    if value is None or isinstance(value, str | int | float | bool):
        return value
    # Lists carry low-cardinality values such as roles or status buckets.
    if isinstance(value, list | tuple | set):
        return [_sanitize_log_value(item) for item in value]
    # Dictionaries carry structured context; keys are stringified so the JSON output stays stable across runs.
    if isinstance(value, dict):
        return {str(key): _sanitize_log_value(item) for key, item in value.items()}
    # Exceptions are summarized so stack traces and raw messages do not dominate every line.
    if isinstance(value, BaseException):
        return {"error_type": type(value).__name__, "error": str(value)[:200]}
    return type(value).__name__


def _json_default(value: Any) -> str:
    """Serialize unexpected values as type names instead of failing the log write.

    A dropped log line would hide the very event an operator is trying to trace, so an unserializable value degrades to its type.

    Args:
        value: Value the JSON encoder cannot serialize.

    Returns:
        Class name used as a bounded placeholder in the emitted log line.
    """
    return type(value).__name__
