"""
Logging setup for the Python agent service.

This file gives local runs and Docker logs the same JSON-line shape that the
Symfony side emits. Use `LOG_FORMAT=json` when you want `analyze-logs.py` to
join FastAPI, Mercure, role, and summary events for one browser session.
Console output remains the default so existing tests and local debugging stay readable.
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

    It hoists `session_id` and `correlation_id` to top-level fields so the log
    analysis script can join browser, PHP, FastAPI, role, and Mercure events.
    Use this formatter only for structured process logs; do not pass transcript text.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Return one JSON object for a browser-visible process event.

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
        # Correlation IDs may be absent during startup before a browser action exists.
        if "correlation_id" in extra_fields:
            event["correlation_id"] = extra_fields.pop("correlation_id")
        # Session IDs exist only after the user creates or resumes a recording.
        if "session_id" in extra_fields:
            event["session_id"] = extra_fields.pop("session_id")

        event.update(extra_fields)

        if record.exc_info:
            # JSON is the dev default, so it must carry the same traceback a plain log would show.
            if "error_type" not in event:
                event["error_type"] = record.exc_info[0].__name__
            event["traceback"] = "".join(traceback.format_exception(*record.exc_info))

        return json.dumps(event, ensure_ascii=False, default=_json_default)

    def _extra_fields(self, record: logging.LogRecord) -> dict[str, Any]:
        """Collect non-standard fields supplied through `logger.*(..., extra=...)`.

        Args:
            record: Python log record for the current service event.

        Returns:
            Extra attributes safe for JSON serialization; empty means no process context was supplied.
        """
        fields: dict[str, Any] = {}
        # Each non-standard attribute is an explicit field the caller wanted in the process report.
        for key, value in record.__dict__.items():
            # Standard logging internals would make the JSON line noisy and hard to compare.
            if key in _STANDARD_LOG_FIELDS or key.startswith("_"):
                continue
            fields[key] = _sanitize_log_value(value)
        return fields


def configure_logging() -> None:
    """Install the process logger once for FastAPI, workers, and scripts.

    Use `LOG_FORMAT=json` for machine-readable local logs; otherwise console output stays concise.
    The root handler is set to `LOG_LEVEL` so INFO process events are captured in Docker logs.
    """
    global _CONFIGURED

    # Import-time setup must be idempotent because tests can import the API module more than once.
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

    Args:
        value: Extra field value supplied by app code; `None` means the field is absent to the user.

    Returns:
        JSON-ready value; unsupported objects become class names instead of raw representations.
    """
    # Plain scalar values are already safe to render into process-quality reports.
    if value is None or isinstance(value, str | int | float | bool):
        return value
    # Lists carry low-cardinality values such as roles or status buckets.
    if isinstance(value, list | tuple | set):
        return [_sanitize_log_value(item) for item in value]
    # Dictionaries carry structured context; keys are stringified for stable JSON output.
    if isinstance(value, dict):
        return {str(key): _sanitize_log_value(item) for key, item in value.items()}
    # Exceptions are summarized so stack traces and raw messages do not dominate every line.
    if isinstance(value, BaseException):
        return {"error_type": type(value).__name__, "error": str(value)[:200]}
    return type(value).__name__


def _json_default(value: Any) -> str:
    """Serialize unexpected values as type names instead of failing the log write.

    Args:
        value: Value the JSON encoder cannot serialize.

    Returns:
        Class name used as a bounded placeholder in the emitted log line.
    """
    return type(value).__name__
