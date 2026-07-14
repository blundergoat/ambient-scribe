#!/usr/bin/env python3
"""Run the six-note M08 corrected-transcript fidelity audit.

Use this developer runner when a release audit needs fresh clinician notes from
the accepted PriMock57 sample. It replays one visit at a time, requests its note
before session evidence expires, and saves provenance for later human review.
Preparation mode validates the sample without replaying audio or spending tokens.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import wave
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

import httpx


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
FIXTURE_AUDIO_DIRECTORY = REPOSITORY_ROOT / "tests/fixtures/audio"
CORRECTED_EVALUATOR = REPOSITORY_ROOT / "scripts/eval-corrected-fixtures.sh"
DEFAULT_AGENT_URL = "http://localhost:48101"
FINAL_NOTE_LIMIT = 6
BEDROCK_GENERATION_LIMIT = 12
FATAL_AGENT_LOG_PATTERN = re.compile(
    r"Traceback|websocket\.error|CUDA error|illegal memory",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AuditFixture:
    """Describe one consultation the clinician-note audit must exercise.

    Each row explains why the visit is in the six-note sample and preserves the
    WER stratum or historical defect that a release reviewer needs to inspect.
    """

    stem: str
    audit_reason: str


AUDIT_FIXTURES = (
    AuditFixture(
        "primock57-day5-consultation03-im-feeling-very-anxious",
        "best corrected non-overlap WER in the accepted M06 corpus (16.6%)",
    ),
    AuditFixture(
        "primock57-day1-consultation04-i-dont-feel-well-i-have-a-cough-and-runny-nose",
        "nearest corrected non-overlap WER to the accepted corpus median (21.5%)",
    ),
    AuditFixture(
        "primock57-day1-consultation07-i-have-a-cough-and-cold",
        "worst corrected non-overlap WER in the accepted M06 corpus (32.6%)",
    ),
    AuditFixture(
        "primock57-day1-consultation03-i-have-terrible-headache",
        "historical note-fidelity defect consultation",
    ),
    AuditFixture(
        "primock57-day3-consultation01-lips-swelling-after-eating-a-sandwich",
        "historical note-fidelity defect consultation",
    ),
    AuditFixture(
        "primock57-day5-consultation09-tired-all-the-time",
        "historical note-fidelity defect consultation",
    ),
)


def utc_timestamp() -> str:
    """Return the UTC time used to correlate one release-audit artifact set."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def timestamp_slug() -> str:
    """Return the filesystem-safe UTC suffix shown in the audit directory name."""
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def parse_arguments() -> argparse.Namespace:
    """Read the operator's preparation or paid-run choice for this release audit."""
    parser = argparse.ArgumentParser(description=__doc__)
    run_mode = parser.add_mutually_exclusive_group(required=True)
    run_mode.add_argument(
        "--prepare-only",
        action="store_true",
        help="validate fixtures and runtime without replaying audio or requesting notes",
    )
    run_mode.add_argument(
        "--run",
        action="store_true",
        help="run all six replays and request exactly one final note per fixture",
    )
    parser.add_argument(
        "--agent-url",
        default=os.environ.get("M08_AGENT_URL", DEFAULT_AGENT_URL),
        help="local NeMo agent URL; an empty value is rejected before any replay",
    )
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        help="artifact root; omitted creates var/quality/note-fidelity-audit-<UTC>",
    )
    return parser.parse_args()


def audio_path_for(fixture: AuditFixture) -> Path:
    """Return the full WAV path for the consultation a release reviewer will audit."""
    return FIXTURE_AUDIO_DIRECTORY / f"{fixture.stem}.wav"


def audio_duration_seconds(audio_path: Path) -> float:
    """Measure how much real-time replay the selected consultation requires."""
    with wave.open(str(audio_path), "rb") as audio_file:
        return audio_file.getnframes() / audio_file.getframerate()


def read_json_object(artifact_path: Path) -> dict[str, Any]:
    """Load one saved audit object; missing or non-object evidence stops the run."""
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    # A list or scalar cannot carry the session fields the clinician-note audit needs.
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected a JSON object in {artifact_path}")
    return payload


def write_json_object(artifact_path: Path, payload: dict[str, Any]) -> None:
    """Save one stable, readable evidence object for the release reviewer."""
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_command(
    command: list[str],
    *,
    output_path: Path | None = None,
    environment: dict[str, str] | None = None,
    timeout_seconds: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one local audit command and preserve output when the reviewer needs it."""
    completed_command = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    # Saved command output lets the reviewer diagnose a replay without rerunning paid work.
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            completed_command.stdout + completed_command.stderr,
            encoding="utf-8",
        )
    # A failed prerequisite or replay cannot produce trustworthy clinician-note evidence.
    if completed_command.returncode != 0:
        output_excerpt = (completed_command.stderr or completed_command.stdout)[-1200:]
        raise RuntimeError(
            f"command failed ({completed_command.returncode}): {' '.join(command)}\n"
            f"{output_excerpt}"
        )
    return completed_command


def require_fixture_inputs() -> list[dict[str, Any]]:
    """Validate the exact six consultation inputs before any clinician-note request."""
    fixture_manifest: list[dict[str, Any]] = []
    # Every selected visit needs audio plus separate doctor and patient reference grids.
    for fixture in AUDIT_FIXTURES:
        fixture_audio_path = audio_path_for(fixture)
        doctor_grid_path = fixture_audio_path.with_suffix(".doctor.TextGrid")
        patient_grid_path = fixture_audio_path.with_suffix(".patient.TextGrid")
        # Missing source evidence would make this fixture impossible to score or audit fairly.
        if not all(
            source_path.is_file()
            for source_path in (
                fixture_audio_path,
                doctor_grid_path,
                patient_grid_path,
            )
        ):
            raise RuntimeError(f"fixture input is incomplete: {fixture.stem}")
        fixture_manifest.append(
            {
                "stem": fixture.stem,
                "audit_reason": fixture.audit_reason,
                "audio_seconds": round(audio_duration_seconds(fixture_audio_path), 3),
                "audio_path": str(fixture_audio_path.relative_to(REPOSITORY_ROOT)),
            }
        )
    return fixture_manifest


def require_healthy_agent(agent_url: str) -> dict[str, Any]:
    """Confirm the transcription service is ready for the clinician's replay."""
    # An empty URL means there is no service destination, so no replay should start.
    if not agent_url.strip():
        raise RuntimeError("--agent-url cannot be empty")
    response = httpx.get(f"{agent_url.rstrip('/')}/health", timeout=10.0)
    response.raise_for_status()
    health_payload = response.json()
    # Loaded models are required before the user can receive transcript or note evidence.
    if health_payload.get("models_loaded") is not True:
        raise RuntimeError(f"agent models are not loaded: {health_payload}")
    return health_payload


def require_cuda_available() -> str:
    """Confirm NeMo still owns a usable GPU before the clinician audio is replayed."""
    cuda_check = run_command(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "nemo-agent",
            "python",
            "-c",
            "import torch; print(torch.cuda.is_available())",
        ],
        timeout_seconds=30.0,
    )
    cuda_result = cuda_check.stdout.strip()
    # A false CUDA result would invalidate all six real-time transcription samples.
    if cuda_result != "True":
        raise RuntimeError(
            f"CUDA unavailable in nemo-agent: {cuda_result or '<empty>'}"
        )
    return cuda_result


def require_structured_logging() -> str:
    """Require JSON agent events so every generated note has attributable provenance."""
    log_format_check = run_command(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "nemo-agent",
            "sh",
            "-c",
            'printf "%s" "$LOG_FORMAT"',
        ],
        timeout_seconds=30.0,
    )
    log_format = log_format_check.stdout.strip()
    # Console logs omit fields needed to count retries and prove corrected-source generation.
    if log_format != "json":
        raise RuntimeError(
            "paid M08 mode requires nemo-agent LOG_FORMAT=json; prepare-only may use console"
        )
    return log_format


def evidence_directory_from(arguments: argparse.Namespace) -> Path:
    """Choose where this release audit keeps its replay and note evidence."""
    # An omitted path gets a unique release-audit directory instead of overwriting earlier evidence.
    if arguments.evidence_dir is None:
        return (
            REPOSITORY_ROOT / "var/quality" / f"note-fidelity-audit-{timestamp_slug()}"
        )
    # Relative paths stay under this repository so the reviewer can reuse handoff commands.
    if not arguments.evidence_dir.is_absolute():
        return REPOSITORY_ROOT / arguments.evidence_dir
    return arguments.evidence_dir


def preparation_payload(
    fixture_manifest: list[dict[str, Any]],
    agent_health: dict[str, Any],
    cuda_result: str,
) -> dict[str, Any]:
    """Build the no-token readiness report the operator sees before approving the run."""
    total_audio_seconds = 0.0
    # The total tells the operator how long the six visits will occupy the single GPU.
    for fixture_row in fixture_manifest:
        total_audio_seconds += float(fixture_row["audio_seconds"])
    return {
        "prepared_at": utc_timestamp(),
        "fixture_count": len(fixture_manifest),
        "total_audio_seconds": round(total_audio_seconds, 3),
        "total_audio_minutes": round(total_audio_seconds / 60, 1),
        "final_note_limit": FINAL_NOTE_LIMIT,
        "bedrock_generation_minimum": FINAL_NOTE_LIMIT,
        "bedrock_generation_limit": BEDROCK_GENERATION_LIMIT,
        "pace": "1x",
        "chunk_ms": 5000,
        "agent_health": agent_health,
        "cuda_available": cuda_result == "True",
        "fixtures": fixture_manifest,
    }


def start_agent_log_capture(
    evidence_directory: Path,
    run_started_at: str,
) -> tuple[subprocess.Popen[str], TextIO]:
    """Capture rotating agent logs while the clinician fixtures and notes are processed."""
    agent_log_path = evidence_directory / "agent-structured.log"
    agent_log_handle = agent_log_path.open("a", encoding="utf-8")
    capture_process = subprocess.Popen(
        [
            "docker",
            "compose",
            "logs",
            "-f",
            "--no-color",
            "--since",
            run_started_at,
            "nemo-agent",
        ],
        cwd=REPOSITORY_ROOT,
        stdout=agent_log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return capture_process, agent_log_handle


def stop_agent_log_capture(
    capture_process: subprocess.Popen[str] | None,
    agent_log_handle: TextIO | None,
) -> None:
    """Close the evidence stream after the last note or a stopped audit."""
    # An absent process means the audit failed before Docker log capture started.
    if capture_process is not None:
        capture_process.terminate()
        try:
            capture_process.wait(timeout=10.0)
        # Example: Docker can keep the log stream open while a container is restarting.
        except subprocess.TimeoutExpired:
            capture_process.kill()
            capture_process.wait(timeout=5.0)
    # An absent handle means no file was opened, so there is nothing for the reviewer to flush.
    if agent_log_handle is not None:
        agent_log_handle.flush()
        agent_log_handle.close()


def record_health_until_stopped(
    stop_monitoring: threading.Event,
    evidence_directory: Path,
    agent_url: str,
) -> None:
    """Record agent readiness throughout replay so a later failure has timing evidence."""
    health_log_path = evidence_directory / "health-monitor.jsonl"
    # Keep sampling while any consultation is still using the transcription service.
    while not stop_monitoring.is_set():
        health_row: dict[str, Any] = {"checked_at": utc_timestamp()}
        try:
            health_row["health"] = require_healthy_agent(agent_url)
            health_row["ok"] = True
        # Example: the clinician's replay can overlap a container crash or model restart.
        except Exception as error:  # noqa: BLE001 - evidence must record every runtime failure.
            health_row["ok"] = False
            health_row["error_type"] = type(error).__name__
            health_row["error"] = str(error)[:500]
        with health_log_path.open("a", encoding="utf-8") as health_log:
            health_log.write(json.dumps(health_row, sort_keys=True) + "\n")
        stop_monitoring.wait(30.0)


def capture_gpu_sample_after_start(
    stop_monitoring: threading.Event,
    evidence_directory: Path,
) -> None:
    """Save the required ten-second GPU sample while the first visit is replaying."""
    # A run that already stopped does not need a late GPU command against an idle service.
    if stop_monitoring.wait(10.0):
        return
    gpu_report_path = evidence_directory / "gpu-sample-10s.txt"
    # A resumed audit keeps the first visit's GPU evidence and writes a separate sample.
    if gpu_report_path.exists():
        gpu_report_path = (
            evidence_directory / f"gpu-sample-resume-{timestamp_slug()}-10s.txt"
        )
    try:
        gpu_report = run_command(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "nemo-agent",
                "nvidia-smi",
                "--query-gpu=timestamp,name,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader",
            ],
            timeout_seconds=30.0,
        )
        gpu_report_path.write_text(gpu_report.stdout, encoding="utf-8")
    # Example: the single GPU can disappear after health passed but before speech reaches NeMo.
    except Exception as error:  # noqa: BLE001 - the saved failure becomes a hard run gate.
        gpu_report_path.write_text(
            f"ERROR {type(error).__name__}: {error}\n",
            encoding="utf-8",
        )


def assert_no_fatal_agent_logs(agent_log_path: Path) -> None:
    """Stop between visits when agent logs show a transcript-invalidating runtime failure."""
    # Log capture may still be starting, so an empty file is checked again after each later fixture.
    if not agent_log_path.exists():
        return
    agent_log_text = agent_log_path.read_text(encoding="utf-8", errors="replace")
    fatal_log_match = FATAL_AGENT_LOG_PATTERN.search(agent_log_text)
    # A fatal match means later clinician notes cannot be compared on a healthy common runtime.
    if fatal_log_match is not None:
        raise RuntimeError(
            f"fatal agent log matched {fatal_log_match.group(0)!r}; see {agent_log_path}"
        )


def run_fixture_replay(
    fixture: AuditFixture,
    evidence_directory: Path,
    agent_url: str,
) -> Path:
    """Replay and correct one consultation before its session evidence can expire."""
    replay_root = evidence_directory / "replays"
    fixture_artifact_directory = replay_root / fixture.stem
    eval_log_path = fixture_artifact_directory / "eval.log"
    evaluator_environment = os.environ.copy()
    evaluator_environment.update(
        {
            "AGENT_HTTP_URL": agent_url.rstrip("/"),
            "AGENT_WS_URL": agent_url.rstrip("/").replace("http://", "ws://", 1),
            "CORRECTED_FIXTURE_RUN_DIR": str(replay_root),
            "EVAL_PACE": "1x",
            "EVAL_CHUNK_MS": "5000",
            "EVAL_REQUIRE_STRUCTURED_LOGS": "1",
        }
    )
    print(f"[{utc_timestamp()}] replaying {fixture.stem}", flush=True)
    run_command(
        [str(CORRECTED_EVALUATOR), fixture.stem],
        output_path=eval_log_path,
        environment=evaluator_environment,
        timeout_seconds=audio_duration_seconds(audio_path_for(fixture)) + 900.0,
    )
    return fixture_artifact_directory


def require_ready_correction(fixture_artifact_directory: Path) -> dict[str, Any]:
    """Require a complete corrected transcript before the clinician note is requested."""
    correction_response_path = fixture_artifact_directory / "correction-response.json"
    corrected_transcript_path = fixture_artifact_directory / "corrected-transcript.json"
    live_history_path = fixture_artifact_directory / "live-history.json"
    # These artifacts prove what the browser sent and what the note source selected.
    if not all(
        artifact_path.is_file()
        for artifact_path in (
            correction_response_path,
            corrected_transcript_path,
            live_history_path,
        )
    ):
        raise RuntimeError(
            f"correction artifacts are incomplete in {fixture_artifact_directory}"
        )
    correction_response = read_json_object(correction_response_path)
    # Only ready means the clinician has a corrected source suitable for a fidelity note.
    if correction_response.get("status") != "ready":
        raise RuntimeError(f"correction was not ready: {correction_response}")
    return correction_response


def request_corrected_summary(
    fixture_artifact_directory: Path,
    agent_url: str,
) -> tuple[str, dict[str, Any]]:
    """Request the one final note for this fresh corrected consultation."""
    history_payload = read_json_object(fixture_artifact_directory / "live-history.json")
    session_id = str(history_payload.get("session_id") or "")
    visible_segments = history_payload.get("segments")
    # Missing session identity would send paid work to an unverifiable consultation.
    if not session_id:
        raise RuntimeError("live-history.json has no session_id")
    # Missing or empty rows mean the user has no transcript from which to generate a note.
    if not isinstance(visible_segments, list) or not visible_segments:
        raise RuntimeError("live-history.json has no visible transcript segments")
    summary_request = {"segments": visible_segments}
    write_json_object(
        fixture_artifact_directory / "summary-request.json",
        summary_request,
    )
    print(
        f"[{utc_timestamp()}] requesting corrected-source note for {session_id}",
        flush=True,
    )
    response = httpx.post(
        f"{agent_url.rstrip('/')}/session/{session_id}/summary",
        json=summary_request,
        timeout=600.0,
    )
    response.raise_for_status()
    summary_payload = response.json()
    # A non-object response cannot render the structured note the clinician expects.
    if not isinstance(summary_payload, dict):
        raise RuntimeError("summary response was not a JSON object")
    write_json_object(fixture_artifact_directory / "summary.json", summary_payload)
    return session_id, summary_payload


def note_has_visible_content(summary_payload: dict[str, Any]) -> bool:
    """Return whether the summary contains any note text a clinician can review."""
    note_sections = summary_payload.get("sections")
    # A missing section list can still be usable when the note contains key points.
    if isinstance(note_sections, list):
        # Any non-empty section content gives the clinician a visible SOAP note.
        for note_section in note_sections:
            # Malformed section rows are ignored here and exposed by later audit evidence.
            if (
                isinstance(note_section, dict)
                and str(note_section.get("content") or "").strip()
            ):
                return True
    key_points = summary_payload.get("key_points")
    # Key points alone are still visible note content when the provider leaves sections sparse.
    if isinstance(key_points, list):
        # One non-empty point is enough to avoid misclassifying a sparse note as empty.
        for key_point in key_points:
            # Blank provider strings do not give the clinician anything to review.
            if str(key_point or "").strip():
                return True
    return False


def require_summary_input_contract(summary_payload: dict[str, Any]) -> None:
    """Enforce corrected source and byte-complete context for the clinician note."""
    # Browser-visible input would bypass the corrected transcript this audit exists to measure.
    if summary_payload.get("transcript_source") != "corrected_segments":
        raise RuntimeError(
            f"summary used {summary_payload.get('transcript_source')!r}, not corrected_segments"
        )
    # Truncation would make an apparent omission impossible to distinguish from context loss.
    if summary_payload.get("transcript_truncated") is not False:
        raise RuntimeError("summary transcript was truncated")
    original_transcript_chars = summary_payload.get("original_transcript_chars")
    kept_transcript_chars = summary_payload.get("kept_transcript_chars")
    # Unequal or absent counts mean the full consultation did not reach note generation.
    if (
        not isinstance(original_transcript_chars, int)
        or not isinstance(kept_transcript_chars, int)
        or original_transcript_chars != kept_transcript_chars
    ):
        raise RuntimeError(
            "summary transcript character counts are absent or not byte-identical: "
            f"original={original_transcript_chars!r} kept={kept_transcript_chars!r}"
        )
    # Empty generated content leaves the clinician without a note to audit.
    if not note_has_visible_content(summary_payload):
        raise RuntimeError("summary response contains no visible note content")


def docker_logs_since(run_started_at: str) -> str:
    """Read current agent logs immediately so rotating evidence is not lost."""
    log_result = run_command(
        [
            "docker",
            "compose",
            "logs",
            "--no-color",
            "--since",
            run_started_at,
            "nemo-agent",
        ],
        timeout_seconds=60.0,
    )
    return log_result.stdout


def structured_event_from_log_line(log_line: str) -> dict[str, Any] | None:
    """Extract one JSON agent event; console or non-JSON lines return no event."""
    object_start = log_line.find("{")
    # Docker prefixes without a JSON object cannot prove a structured summary event.
    if object_start < 0:
        return None
    try:
        event_payload = json.loads(log_line[object_start:])
    # Example: a container restart can interleave a prefix with a partial final log line.
    except json.JSONDecodeError:
        return None
    # Arrays and scalar JSON are not canonical agent event records.
    if not isinstance(event_payload, dict):
        return None
    return event_payload


def capture_summary_events(
    session_id: str,
    fixture_artifact_directory: Path,
    run_started_at: str,
) -> tuple[list[dict[str, Any]], int]:
    """Save this note's PHI-safe events and return its observed generation count."""
    current_agent_logs = docker_logs_since(run_started_at)
    summary_events: list[dict[str, Any]] = []
    # Only this consultation's structured summary events belong beside its note evidence.
    for log_line in current_agent_logs.splitlines():
        structured_event = structured_event_from_log_line(log_line)
        # Console or other-session lines do not establish this note's provenance.
        if structured_event is None or structured_event.get("session_id") != session_id:
            continue
        event_name = str(structured_event.get("event") or "")
        # Role and transcript events stay in the replay evidence, not the note event slice.
        if not event_name.startswith("summary."):
            continue
        summary_events.append(structured_event)
    write_json_lines(
        fixture_artifact_directory / "summary-events.jsonl",
        summary_events,
    )
    event_names: list[str] = []
    # Stable names make missing requested/completed events easy to diagnose in one glance.
    for summary_event in summary_events:
        event_names.append(str(summary_event.get("event") or ""))
    # A completed event proves the route returned after generation and publishing succeeded.
    if not any(
        event_name.startswith("summary.completed") for event_name in event_names
    ):
        raise RuntimeError(
            f"structured summary.completed event missing for {session_id}"
        )
    retry_observed = False
    # Attempt zero violations cause exactly one permitted fidelity-guided Bedrock regeneration.
    for summary_event in summary_events:
        event_name = str(summary_event.get("event") or "")
        # A first-draft violation proves the note used its one allowed retry.
        if (
            event_name.startswith("summary.fidelity_violations")
            and summary_event.get("attempt") == 0
        ):
            retry_observed = True
            break
    # One first-draft violation event means the shipped policy made its single allowed redo.
    observed_generation_count = 2 if retry_observed else 1
    return summary_events, observed_generation_count


def write_json_lines(artifact_path: Path, rows: list[dict[str, Any]]) -> None:
    """Save ordered structured events; an empty list creates an empty evidence file."""
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    with artifact_path.open("w", encoding="utf-8") as artifact_file:
        # Each line remains independently readable if the operator inspects a partial run.
        for row in rows:
            artifact_file.write(json.dumps(row, sort_keys=True) + "\n")


def corrected_segment_ids(fixture_artifact_directory: Path) -> set[str]:
    """Return the corrected row IDs a clinician citation is allowed to resolve to."""
    corrected_payload = read_json_object(
        fixture_artifact_directory / "corrected-transcript.json"
    )
    corrected_rows = corrected_payload.get("segments")
    # Missing corrected rows means no returned citation can be verified against the visit.
    if not isinstance(corrected_rows, list) or not corrected_rows:
        raise RuntimeError("corrected transcript has no segments")
    available_segment_ids: set[str] = set()
    # Every non-empty row ID becomes a valid source chip target for this note.
    for corrected_row in corrected_rows:
        # Malformed rows cannot resolve a clinician-facing citation.
        if not isinstance(corrected_row, dict):
            continue
        segment_id = str(corrected_row.get("segment_id") or "")
        # An empty row ID is unusable as a citation target and remains absent from the set.
        if segment_id:
            available_segment_ids.add(segment_id)
    return available_segment_ids


def cited_segment_ids(summary_payload: dict[str, Any]) -> list[str]:
    """Return every source-chip ID emitted across the clinician note sections."""
    citation_ids: list[str] = []
    note_sections = summary_payload.get("sections")
    # A missing section list means there are no section citations to resolve.
    if not isinstance(note_sections, list):
        return citation_ids
    # Each section can cite several corrected transcript rows shown in the source drawer.
    for note_section in note_sections:
        # A malformed provider section has no safe citation list to inspect.
        if not isinstance(note_section, dict):
            continue
        section_citations = note_section.get("citations")
        # Empty or absent citations leave this section visible without source chips.
        if not isinstance(section_citations, list):
            continue
        # Preserve repeats because citation resolution is measured per returned citation.
        for citation in section_citations:
            # A malformed citation counts as an empty unresolved ID in the audit evidence.
            if not isinstance(citation, dict):
                citation_ids.append("")
                continue
            citation_ids.append(str(citation.get("segment_id") or ""))
    return citation_ids


def build_note_provenance(
    fixture: AuditFixture,
    fixture_artifact_directory: Path,
    session_id: str,
    summary_payload: dict[str, Any],
    generation_count: int,
) -> dict[str, Any]:
    """Build the source, citation, flag, and token provenance for one final note."""
    available_segment_ids = corrected_segment_ids(fixture_artifact_directory)
    returned_citation_ids = cited_segment_ids(summary_payload)
    resolved_citation_ids: list[str] = []
    unresolved_citation_ids: list[str] = []
    # Every returned chip is classified so a missing source cannot hide in an aggregate rate.
    for citation_id in returned_citation_ids:
        # A known corrected row gives the clinician a working path back to transcript wording.
        if citation_id in available_segment_ids:
            resolved_citation_ids.append(citation_id)
        else:
            unresolved_citation_ids.append(citation_id)
    citation_count = len(returned_citation_ids)
    # A note with no citations records a null rate rather than claiming perfect resolution.
    citation_resolution_rate = (
        round(len(resolved_citation_ids) / citation_count, 4)
        if citation_count
        else None
    )
    section_unverified_count = 0
    section_low_confidence_count = 0
    note_sections = summary_payload.get("sections")
    # Keep fidelity and wording-review counts separate for the clinician.
    if isinstance(note_sections, list):
        # Each valid section contributes its own visible warning counts.
        for note_section in note_sections:
            # Human review handles malformed sections without inventing warning counts.
            if not isinstance(note_section, dict):
                continue
            unverified_rows = note_section.get("unverified")
            low_confidence_rows = note_section.get("low_confidence")
            # An absent or non-list fidelity field means no section sentences were visibly flagged.
            if isinstance(unverified_rows, list):
                section_unverified_count += len(unverified_rows)
            # An absent or non-list confidence field means no wording-review flag was attached.
            if isinstance(low_confidence_rows, list):
                section_low_confidence_count += len(low_confidence_rows)
    unverified_key_points = summary_payload.get("unverified_key_points")
    # An absent key-point warning list means no key point was visibly marked unverified.
    key_point_unverified_count = (
        len(unverified_key_points) if isinstance(unverified_key_points, list) else 0
    )
    return {
        "fixture": fixture.stem,
        "audit_reason": fixture.audit_reason,
        "session_id": session_id,
        "captured_at": utc_timestamp(),
        "transcript_source": summary_payload.get("transcript_source"),
        "transcript_truncated": summary_payload.get("transcript_truncated"),
        "original_transcript_chars": summary_payload.get("original_transcript_chars"),
        "kept_transcript_chars": summary_payload.get("kept_transcript_chars"),
        "corrected_segment_count": len(available_segment_ids),
        "citation_count": citation_count,
        "resolved_citation_count": len(resolved_citation_ids),
        "unresolved_citation_count": len(unresolved_citation_ids),
        "citation_resolution_rate": citation_resolution_rate,
        "unresolved_citation_ids": unresolved_citation_ids,
        "section_unverified_count": section_unverified_count,
        "key_point_unverified_count": key_point_unverified_count,
        "low_confidence_count": section_low_confidence_count,
        "bedrock_generations_observed": generation_count,
        "semantic_audit_status": "pending_human_review",
    }


def write_audit_template(
    fixture: AuditFixture,
    fixture_artifact_directory: Path,
    session_id: str,
) -> None:
    """Create the per-note rubric the reviewer completes against corrected rows."""
    write_json_object(
        fixture_artifact_directory / "audit-template.json",
        {
            "fixture": fixture.stem,
            "session_id": session_id,
            "status": "pending_human_review",
            "known_fabrication_families": {
                "uncertainty_resolved": [],
                "invented_denial": [],
                "screening_as_exam": [],
                "patient_state_without_evidence": [],
                "non_verbatim_quote": [],
            },
            "novel_unsupported_shapes": [],
            "fidelity_flags": [],
            "low_confidence_flags": [],
            "split_question_composite_denial_false_positives": [],
            "supported_sentence_count": None,
            "unsupported_unflagged_sentence_count": None,
            "review_notes": [],
        },
    )


def initial_run_state(evidence_directory: Path) -> dict[str, Any]:
    """Create or resume the paid-work ledger that prevents duplicate note requests."""
    run_state_path = evidence_directory / "run-state.json"
    # A prior partial run resumes only completed notes and never silently spends on them again.
    if run_state_path.is_file():
        return read_json_object(run_state_path)
    return {
        "status": "running",
        "started_at": utc_timestamp(),
        "requested_fixtures": [],
        "completed_fixtures": [],
        "terminal_contract_failures": [],
        "summary_requests": 0,
        "bedrock_generations_observed": 0,
        "final_note_limit": FINAL_NOTE_LIMIT,
        "bedrock_generation_limit": BEDROCK_GENERATION_LIMIT,
    }


def save_run_state(evidence_directory: Path, run_state: dict[str, Any]) -> None:
    """Persist paid-work totals after each note so interruption cannot duplicate spend."""
    run_state["updated_at"] = utc_timestamp()
    write_json_object(evidence_directory / "run-state.json", run_state)


def completed_fixture_stems(run_state: dict[str, Any]) -> set[str]:
    """Return visits whose final notes already exist and must not be regenerated."""
    completed_fixtures = run_state.get("completed_fixtures")
    # An absent or malformed ledger is unsafe because duplicate paid requests could follow.
    if not isinstance(completed_fixtures, list):
        raise RuntimeError("run-state completed_fixtures is not a list")
    # Each saved stem represents one immutable final note that resume mode must preserve.
    return {str(fixture_stem) for fixture_stem in completed_fixtures}


def requested_fixture_stems(run_state: dict[str, Any]) -> set[str]:
    """Return visits that already spent their one approved final-note request."""
    requested_fixtures = run_state.get("requested_fixtures")
    # An older empty audit state can safely adopt the new ledger before its first paid request.
    if requested_fixtures is None and int(run_state.get("summary_requests", 0)) == 0:
        requested_fixtures = []
        run_state["requested_fixtures"] = requested_fixtures
    # A missing or malformed paid-work ledger could allow duplicate provider requests.
    if not isinstance(requested_fixtures, list):
        raise RuntimeError("run-state requested_fixtures is not a list")
    # Each requested stem has consumed its only approved note, even if later validation failed.
    return {str(fixture_stem) for fixture_stem in requested_fixtures}


def terminal_contract_failure_stems(
    run_state: dict[str, Any],
    completed_stems: set[str],
    requested_stems: set[str],
) -> set[str]:
    """Preserve an invalid returned note so the audit can continue without repeat spend."""
    terminal_failures = run_state.get("terminal_contract_failures")
    # The stopped c07 run predates this ledger field, so adopt only its exact saved failure.
    if terminal_failures is None:
        terminal_failures = []
        # A truncation response is terminal evidence because asking again would replace it.
        if (
            run_state.get("status") == "failed"
            and run_state.get("error") == "summary transcript was truncated"
        ):
            interrupted_stems = requested_stems - completed_stems
            # Exactly one interrupted visit proves which returned note failed its contract.
            if len(interrupted_stems) != 1:
                raise RuntimeError(
                    "cannot identify one terminal truncation fixture from run-state"
                )
            terminal_failures.append(next(iter(interrupted_stems)))
        run_state["terminal_contract_failures"] = terminal_failures
    # A malformed terminal ledger could skip an unrequested visit or hide duplicate spend.
    if not isinstance(terminal_failures, list):
        raise RuntimeError("run-state terminal_contract_failures is not a list")
    # Each terminal stem names one returned note retained for human contract review.
    terminal_stems = {str(fixture_stem) for fixture_stem in terminal_failures}
    # A terminal result must already own a paid request before resume may skip it.
    if not terminal_stems.issubset(requested_stems):
        raise RuntimeError("terminal contract failure has no recorded summary request")
    # A visit cannot be both a valid completed note and a terminal contract failure.
    if terminal_stems & completed_stems:
        raise RuntimeError("terminal contract failure is also marked complete")
    return terminal_stems


def require_remaining_budget(run_state: dict[str, Any]) -> None:
    """Stop before a request that could exceed the approved six-note/twelve-call cap."""
    summary_requests = run_state.get("summary_requests")
    observed_generations = run_state.get("bedrock_generations_observed")
    # Missing counters make it impossible to prove the user's approved spend boundary.
    if not isinstance(summary_requests, int) or not isinstance(
        observed_generations, int
    ):
        raise RuntimeError("run-state token counters are missing or malformed")
    # Six completed HTTP requests are the full approved sample; a seventh is never allowed.
    if summary_requests >= FINAL_NOTE_LIMIT:
        raise RuntimeError("six final note requests already recorded; refusing another")
    # A remaining note can consume at most two generations under the shipped retry policy.
    if observed_generations + 2 > BEDROCK_GENERATION_LIMIT:
        raise RuntimeError("next note could exceed the twelve-generation Bedrock cap")


def run_paid_audit(
    arguments: argparse.Namespace,
    evidence_directory: Path,
    preparation: dict[str, Any],
) -> str:
    """Generate six fresh corrected-source notes and preserve their release evidence."""
    require_structured_logging()
    evidence_directory.mkdir(parents=True, exist_ok=True)
    write_json_object(evidence_directory / "preparation.json", preparation)
    run_state = initial_run_state(evidence_directory)
    # An empty start time would make session-specific log provenance impossible to recover.
    run_started_at = str(run_state.get("started_at") or "")
    # Missing start time would prevent reliable log capture across a resumed run.
    if not run_started_at:
        raise RuntimeError("run-state has no started_at timestamp")

    capture_process: subprocess.Popen[str] | None = None
    agent_log_handle: TextIO | None = None
    stop_monitoring = threading.Event()
    health_monitor = threading.Thread(
        target=record_health_until_stopped,
        args=(stop_monitoring, evidence_directory, arguments.agent_url),
        daemon=True,
    )
    gpu_sampler = threading.Thread(
        target=capture_gpu_sample_after_start,
        args=(stop_monitoring, evidence_directory),
        daemon=True,
    )
    try:
        capture_process, agent_log_handle = start_agent_log_capture(
            evidence_directory,
            run_started_at,
        )
        health_monitor.start()
        gpu_sampler.start()
        completed_stems = completed_fixture_stems(run_state)
        requested_stems = requested_fixture_stems(run_state)
        terminal_failure_stems = terminal_contract_failure_stems(
            run_state,
            completed_stems,
            requested_stems,
        )
        # Resume keeps a failed note visible while later visits remain valid audit work.
        if terminal_failure_stems:
            run_state["status"] = "running_with_preserved_contract_failure"
        else:
            run_state["status"] = "running"
        run_state["resumed_at"] = utc_timestamp()
        save_run_state(evidence_directory, run_state)
        # Each visit is corrected and summarized immediately so session TTL cannot drop its source.
        for fixture in AUDIT_FIXTURES:
            # A saved final note is immutable paid evidence and is skipped during resume.
            if fixture.stem in completed_stems:
                print(
                    f"[{utc_timestamp()}] keeping completed {fixture.stem}", flush=True
                )
                continue
            # A returned truncated note stays visible as failed evidence and is never regenerated.
            if fixture.stem in terminal_failure_stems:
                print(
                    f"[{utc_timestamp()}] preserving terminal contract failure "
                    f"{fixture.stem}",
                    flush=True,
                )
                continue
            # A returned but invalid note remains evidence; resume must not spend to replace it.
            if fixture.stem in requested_stems:
                raise RuntimeError(
                    f"{fixture.stem} already consumed its final-note request but did not complete; "
                    "inspect the saved response instead of regenerating it"
                )
            require_remaining_budget(run_state)
            require_healthy_agent(arguments.agent_url)
            require_cuda_available()
            fixture_artifact_directory = run_fixture_replay(
                fixture,
                evidence_directory,
                arguments.agent_url,
            )
            require_ready_correction(fixture_artifact_directory)
            require_healthy_agent(arguments.agent_url)
            assert_no_fatal_agent_logs(evidence_directory / "agent-structured.log")
            session_id, summary_payload = request_corrected_summary(
                fixture_artifact_directory,
                arguments.agent_url,
            )
            run_state["summary_requests"] = int(run_state["summary_requests"]) + 1
            requested_fixtures = run_state["requested_fixtures"]
            # The paid-work ledger stays a list so every approved request has one fixture owner.
            if not isinstance(requested_fixtures, list):
                raise RuntimeError("run-state requested_fixtures changed shape")
            requested_fixtures.append(fixture.stem)
            requested_stems.add(fixture.stem)
            save_run_state(evidence_directory, run_state)
            time.sleep(1.0)
            _, generation_count = capture_summary_events(
                session_id,
                fixture_artifact_directory,
                run_started_at,
            )
            run_state["bedrock_generations_observed"] = (
                int(run_state["bedrock_generations_observed"]) + generation_count
            )
            # Observed retries must remain inside the explicit twelve-generation approval.
            if (
                int(run_state["bedrock_generations_observed"])
                > BEDROCK_GENERATION_LIMIT
            ):
                raise RuntimeError("observed Bedrock generation count exceeded twelve")
            save_run_state(evidence_directory, run_state)
            require_summary_input_contract(summary_payload)
            note_provenance = build_note_provenance(
                fixture,
                fixture_artifact_directory,
                session_id,
                summary_payload,
                generation_count,
            )
            write_json_object(
                fixture_artifact_directory / "summary-provenance.json",
                note_provenance,
            )
            write_audit_template(fixture, fixture_artifact_directory, session_id)
            completed_fixtures = run_state["completed_fixtures"]
            # The ledger shape was checked before the loop and remains a list throughout the run.
            if not isinstance(completed_fixtures, list):
                raise RuntimeError("run-state completed_fixtures changed shape")
            completed_fixtures.append(fixture.stem)
            completed_stems.add(fixture.stem)
            save_run_state(evidence_directory, run_state)
            assert_no_fatal_agent_logs(evidence_directory / "agent-structured.log")
            print(
                f"[{utc_timestamp()}] saved note {len(completed_stems)}/{FINAL_NOTE_LIMIT}; "
                f"observed generations={run_state['bedrock_generations_observed']}",
                flush=True,
            )
        accounted_stems = completed_stems | terminal_failure_stems
        # The gate requires a returned result for every visit, including the preserved failed note.
        if len(accounted_stems) != FINAL_NOTE_LIMIT:
            raise RuntimeError(
                f"accounted for {len(accounted_stems)} notes, expected {FINAL_NOTE_LIMIT}"
            )
        # The final status keeps the failed source contract prominent for the release verdict.
        if terminal_failure_stems:
            final_status = "complete_pending_human_audit_with_contract_failure"
        else:
            final_status = "complete_pending_human_audit"
        run_state["status"] = final_status
        run_state["completed_at"] = utc_timestamp()
        save_run_state(evidence_directory, run_state)
    # Example: a correction, provider call, or GPU check can stop one fixture mid-run.
    except Exception as error:
        run_state["status"] = "failed"
        run_state["failed_at"] = utc_timestamp()
        run_state["error_type"] = type(error).__name__
        run_state["error"] = str(error)[:1000]
        save_run_state(evidence_directory, run_state)
        raise
    finally:
        stop_monitoring.set()
        stop_agent_log_capture(capture_process, agent_log_handle)
        # Started monitors finish quickly so their final evidence is flushed before the sentinel.
        if health_monitor.is_alive():
            health_monitor.join(timeout=5.0)
        # The GPU sample may still be inside its ten-second gate on an early failure.
        if gpu_sampler.is_alive():
            gpu_sampler.join(timeout=12.0)
    return final_status


def write_exit_sentinel(
    evidence_directory: Path,
    status: str,
    error: Exception | None = None,
) -> None:
    """Write the detached-run result the operator can poll without reading rotating logs."""
    sentinel_payload: dict[str, Any] = {
        "status": status,
        "finished_at": utc_timestamp(),
    }
    # A failed run exposes only bounded operational context, not transcript or note text.
    if error is not None:
        sentinel_payload["error_type"] = type(error).__name__
        sentinel_payload["error"] = str(error)[:1000]
    write_json_object(evidence_directory / "exit-sentinel.json", sentinel_payload)


def main() -> int:
    """Validate or execute the M08 release audit from one operator command."""
    arguments = parse_arguments()
    fixture_manifest = require_fixture_inputs()
    agent_health = require_healthy_agent(arguments.agent_url)
    cuda_result = require_cuda_available()
    preparation = preparation_payload(fixture_manifest, agent_health, cuda_result)
    # Preparation proves duration and runtime readiness without creating paid output.
    if arguments.prepare_only:
        print(json.dumps(preparation, indent=2, sort_keys=True))
        return 0

    evidence_directory = evidence_directory_from(arguments)
    print(f"M08 evidence: {evidence_directory}", flush=True)
    try:
        final_status = run_paid_audit(arguments, evidence_directory, preparation)
        write_exit_sentinel(evidence_directory, final_status)
    # Example: a replay can stop because correction is unavailable or Bedrock returns an error.
    except Exception as error:  # noqa: BLE001 - the detached sentinel must capture all failures.
        write_exit_sentinel(evidence_directory, "failed", error)
        raise
    return 0


# The operator invokes this file directly after approving the bounded Bedrock run.
if __name__ == "__main__":
    sys.exit(main())
