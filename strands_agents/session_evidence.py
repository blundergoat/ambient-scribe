"""Persist per-session evidence for offline transcription and note analysis.

Use after a synthetic consultation so live rows, corrected rows, the clinical note, and its fidelity trace survive browser and session expiry.
Evidence writes are best-effort: a storage failure is logged and never blocks the clinician's request.
Bundles contain full transcript and note wording, so choose `SESSION_EVIDENCE_DIR` carefully or set `SESSION_EVIDENCE_ENABLED=0`.
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
# Owner-only: the bundles contain full transcript and clinical-note wording.
EVIDENCE_DIR_MODE = 0o700
EVIDENCE_FILE_MODE = 0o600


def evidence_enabled() -> bool:
    """Report whether session evidence should be written.

    Checked before every bundle write, so an environment that must not retain transcript wording can switch the channel off.

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

    Each visit gets its own directory, so reviewing a consultation means opening one folder rather than filtering a shared log.

    Args:
        session_id: Browser session UUID; each visit gets its own directory.
        base_directory: Explicit root from tests or scripts; null uses environment/default.

    Returns:
        Directory path under a gitignored root; it is not created here.
    """
    resolved_directory = base_directory

    # A null directory means the caller wants whatever root the operator configured for this environment.
    if resolved_directory is None:
        resolved_directory = os.environ.get("SESSION_EVIDENCE_DIR", "")

    # An unset or blank environment value falls back to the repo-local var directory.
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

    Called at each visit milestone, so a consultation that has finalized but not yet been summarized still leaves usable evidence.

    Args:
        session_id: Browser session UUID; empty means there is nothing to key the bundle on.
        name: File stem such as `live-history`; the `.json` suffix is added here.
        payload: JSON-safe record; empty still writes so the absence is visible later.
        base_directory: Explicit root from tests or scripts; null uses environment/default.

    Returns:
        Path written, or None when evidence is disabled or the write failed. A null never reaches the clinician: the caller
        continues with the request it was already serving.
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
        # These files hold complete transcript and clinical-note wording, and the agent runs as root against a bind mount.
        #
        # The owner is therefore set explicitly rather than inherited, because the default umask would leave the bundles
        # readable by every account on the host. chmod runs even when the directory already existed, since mkdir ignores its mode then.
        directory.chmod(EVIDENCE_DIR_MODE)
        destination = directory / f"{name}.json"
        # Staging under a temporary name keeps a reader from seeing a half-written bundle if a long visit finalizes
        # while someone is already inspecting the directory.
        staging = destination.with_suffix(".json.tmp")
        staging.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
        # Tighten before the rename, because replace() carries the staging file's mode: the bundle is never briefly
        # world-readable under its final name.
        staging.chmod(EVIDENCE_FILE_MODE)
        staging.replace(destination)
    except Exception as write_error:  # pragma: no cover - requires a real filesystem permission or storage failure.
        # Example: SESSION_EVIDENCE_DIR points at a bind mount the container cannot write to, so the bundle is lost.
        # The clinician sees nothing: their visit finalizes, correction runs, and the note renders exactly as it would have.
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

    Reads the running process environment rather than compose defaults, because a recreate can leave those disagreeing and
    only the live value explains a result. Secret-bearing names are reported as set or unset, never by value.

    Returns:
        Model, engine, and decoder settings; unset variables are omitted rather than reported as empty.
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
