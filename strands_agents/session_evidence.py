"""
Per-session evidence bundles for offline transcription and note analysis.

A manual test consultation used to be reconstructed by copying three browser
views out by hand. The dev panel is a capped rolling buffer, the clinical note is
published once and never stored, and the session store is in-memory with a TTL -
so a capture taken late lost most of the visit and all of the note.

This module writes the same material to disk as the visit produces it: the live
rows at finalize, the corrected rows when correction completes, and the note plus
its fidelity trace when the summary completes. Nothing needs to be running
alongside the browser and there is no window to miss.

Every write is best-effort. Evidence is a diagnostic side channel, so a failure
here logs and returns rather than breaking a clinician-facing request.

This is developer evidence for a proof-of-concept driven by synthetic seed
consultations. It deliberately stores full transcript and note wording, which is
what makes accuracy analysis possible at all. Point `SESSION_EVIDENCE_DIR` at a
directory you are willing to fill with transcript text, and set
`SESSION_EVIDENCE_ENABLED=0` to turn the whole thing off.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)

EVIDENCE_SCHEMA_VERSION = 1
EVIDENCE_DEFAULT_DIR = Path("var/session-evidence")


def evidence_enabled() -> bool:
    """Report whether session evidence should be written.

    Returns:
        True unless an operator set `SESSION_EVIDENCE_ENABLED` to a false value.
    """
    raw = os.environ.get("SESSION_EVIDENCE_ENABLED", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def session_evidence_dir(
    session_id: str,
    *,
    base_directory: Path | str | None = None,
) -> Path:
    """Resolve the directory holding one session's evidence files.

    Args:
        session_id: Browser session UUID; each visit gets its own directory.
        base_directory: Explicit root from tests or scripts; null uses environment/default.

    Returns:
        Directory path under a gitignored root; it is not created here.
    """
    resolved_directory = base_directory

    # Null directory means callers want the operator-configured default path.
    if resolved_directory is None:
        resolved_directory = os.environ.get("SESSION_EVIDENCE_DIR", "")

    # Empty environment values fall back to the repo-local var directory.
    if resolved_directory in (None, ""):
        resolved_directory = EVIDENCE_DEFAULT_DIR

    return Path(resolved_directory) / session_id


def write_session_evidence(
    session_id: str,
    name: str,
    payload: dict[str, Any],
    *,
    base_directory: Path | str | None = None,
) -> Path | None:
    """Write one evidence file for a session, overwriting any earlier copy.

    Args:
        session_id: Browser session UUID; empty means there is nothing to key the bundle on.
        name: File stem such as `live-history`; the `.json` suffix is added here.
        payload: JSON-safe record; empty still writes so the absence is visible later.
        base_directory: Explicit root from tests or scripts; null uses environment/default.

    Returns:
        Path written, or None when evidence is disabled or the write failed.
    """
    # An unkeyed bundle cannot be matched back to a visit, so there is nothing useful to save.
    if not session_id or not evidence_enabled():
        return None

    record = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "session_id": session_id,
        "artifact": name,
        "written_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        **payload,
    }

    try:
        directory = session_evidence_dir(session_id, base_directory=base_directory)
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"{name}.json"
        # A temporary file keeps a reader from seeing a half-written bundle if a
        # long visit finalizes while someone is already inspecting the directory.
        staging = destination.with_suffix(".json.tmp")
        staging.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
        staging.replace(destination)
    except Exception as write_error:  # pragma: no cover - filesystem-specific.
        logger.warning(
            "session.evidence_write_failed session_id=%s artifact=%s %s: %s",
            session_id,
            name,
            type(write_error).__name__,
            str(write_error)[:300],
            extra={
                "session_id": session_id,
                "artifact": name,
                "error_type": type(write_error).__name__,
            },
        )
        return None

    logger.info(
        "session.evidence_written session_id=%s artifact=%s path=%s",
        session_id,
        name,
        destination,
        extra={
            "session_id": session_id,
            "artifact": name,
            "evidence_path": str(destination),
        },
    )
    return destination


def runtime_identity() -> dict[str, Any]:
    """Capture the runtime settings an accuracy claim depends on.

    Reads the process environment rather than compose defaults, because a
    recreate can leave those disagreeing and only the running value explains a
    result. Secret-bearing names are reported as set/unset, never by value.

    Returns:
        Model, engine, and decoder settings; unset variables are omitted.
    """
    reported = (
        "NEMO_SESSION_ENGINE",
        "NEMO_STREAM_INPUT_FORMAT",
        "NEMO_SPEAKER_CAP",
        "NEMO_MAX_WORKERS",
        "NEMO_BUFFER_MAX_DURATION",
        "NEMO_CORRECTION_REDIARIZATION",
        "NEMO_STREAMING_CROSSTALK_GUARD",
        "NEMO_STREAMING_SLOT_EVIDENCE",
        "NEMO_STREAMING_MAX_TRANSCRIPT_HOLD_SECONDS",
        "NEMO_MODEL_PROVIDER",
        "MEDICAL_BOOST_ENABLED",
        "MEDICAL_LEXICON_PATH",
        "ROLE_AGENT_MODEL_PROVIDER",
        "ROLE_AGENT_MODEL_ID",
        "SUMMARY_AGENT_MODEL_PROVIDER",
        "SUMMARY_AGENT_MODEL_ID",
        "SESSION_STORAGE",
        "SESSION_TTL_SECONDS",
        "LOG_FORMAT",
    )
    settings = {name: os.environ[name] for name in reported if os.environ.get(name)}

    # Naming a credential as present is useful; printing one into a bundle is not.
    secrets_present = {
        name: bool(os.environ.get(name))
        for name in ("MERCURE_JWT", "AWS_ACCESS_KEY_ID", "AWS_SESSION_TOKEN")
    }

    return {"settings": settings, "secrets_present": secrets_present}
