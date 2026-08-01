#!/usr/bin/env python3
"""Build and verify the flag-off recovery evidence packet.

This verifier deliberately cannot emit a full-campaign pass. It binds the
historical frozen-arm receipts, the amended six-file recovery source delta,
and the sealed replacement replay while requiring corpus-on to remain
NOT_EVALUATED.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import re
import sys
import tempfile
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
LEGACY_ACCEPTANCE_ROOT = Path("var/quality/rediar-m05-acceptance")
SEMANTIC_EVIDENCE_ROOT = Path("var/quality/rediarization-candidate-evaluation")
DEFAULT_PACKET_ROOT = (
    SEMANTIC_EVIDENCE_ROOT / "flag-off-recovery/2026-07-25_recovered-corpus-off"
)
DEFAULT_REPLAY_ROOT = (
    LEGACY_ACCEPTANCE_ROOT / "diagnostics/2026-07-25_d2c09-d3-recovery-only-replay1"
)

ATTESTATION_SCHEMA_VERSION = "ambient-scribe-rediarization-flag-off-recovery/v1"
CAP_SCHEMA_VERSION = "ambient-scribe-rediarization-future-call-cap/v1"
FREEZE_SHA = "dc91eae7508ba7e784f78b589204cd8c27969a5a"
CLAIM_SCOPE = "flag-off recovery availability/correctness only"
REPLACEMENT_FIXTURE = "primock57-day2-consultation09-i-cant-move-my-left-arm"
REPLAY_MANIFEST_RECORDS = 97
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")

RECOVERY_SOURCE_PATHS = (
    "strands_agents/nemo_confidence.py",
    "strands_agents/post_visit_word_timing.py",
    "strands_agents/post_visit_correction.py",
    "tests/python/test_post_visit_correction.py",
    "tests/python/test_post_visit_phrase_accuracy.py",
    "tests/python/test_post_visit_word_timing.py",
)
HISTORICAL_IDENTITY_ARMS = (
    "targets-off",
    "targets-on",
    "corpus-off",
)
CORPUS_CHUNK_VECTOR = {
    "primock57-day1-consultation02-i-have-sore-red-skin": 4,
    "primock57-day1-consultation03-i-have-terrible-headache": 3,
    "primock57-day1-consultation06-hard-to-breathe": 4,
    "primock57-day1-consultation07-i-have-a-cough-and-cold": 5,
    "primock57-day1-consultation08-i-have-dry-itchy-skin": 3,
    (
        "primock57-day2-consultation03-i-cant-hear-very-well-and-my-face-is-a-bit-numb"
    ): 3,
    REPLACEMENT_FIXTURE: 3,
    "primock57-day3-consultation01-lips-swelling-after-eating-a-sandwich": 3,
    "primock57-day5-consultation03-im-feeling-very-anxious": 5,
    "primock57-day5-consultation09-tired-all-the-time": 4,
}

REPLAY_BINDING_NAMES = {
    "result_summary": "result-summary.json",
    "diagnostic_verification": "diagnostic-verification-final.json",
    "call_ledger": "call-ledger.txt",
    "run_identity": "run-identity.txt",
}
REPLAY_STATUS = "pass_with_nonblocking_truth_aligned_scorer_findings"


@dataclass
class VerificationResult:
    """Collect a narrow recovery verdict without promoting the full campaign."""

    failures: list[str] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)
    verdict: str = "FAIL_HYBRID_FLAG_OFF"
    full_campaign_status: str = "NOT_EVALUATED"

    @property
    def ok(self) -> bool:
        """Return true only when every fail-closed gate passed."""
        return not self.failures

    def fail(self, reason: str) -> None:
        """Record one distinct rejection reason for operator review."""
        if reason not in self.failures:
            self.failures.append(reason)

    def finish(self) -> VerificationResult:
        """Set the only successful verdict this recovery gate may emit."""
        if self.ok:
            self.verdict = "PASS_HYBRID_FLAG_OFF"
        return self

    def as_document(self) -> dict[str, Any]:
        """Render stable machine-readable CLI output."""
        return {
            "checks": self.checks,
            "failures": self.failures,
            "full_campaign_status": self.full_campaign_status,
            "ok": self.ok,
            "verdict": self.verdict,
        }


def file_sha256(path: Path) -> str:
    """Hash a file in bounded pieces."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _root_confined_path(
    result: VerificationResult,
    workspace_root: Path,
    raw_path: object,
    label: str,
) -> Path | None:
    """Resolve one manifest path without allowing absolute or parent traversal."""
    if not isinstance(raw_path, str) or not raw_path:
        result.fail(f"{label} path must be a non-empty relative string")
        return None
    if "\\" in raw_path:
        result.fail(f"{label} path must use POSIX separators: {raw_path!r}")
        return None
    relative_path = Path(raw_path)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        result.fail(f"{label} path is not root-confined: {raw_path!r}")
        return None

    resolved_root = workspace_root.resolve()
    resolved_path = (resolved_root / relative_path).resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError:
        result.fail(f"{label} path escapes the workspace root: {raw_path!r}")
        return None
    return resolved_path


def _load_json(
    result: VerificationResult,
    path: Path,
    label: str,
) -> dict[str, Any] | None:
    """Load one JSON object and turn malformed input into a gate failure."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        result.fail(f"{label} is not readable JSON: {error}")
        return None
    if not isinstance(document, dict):
        result.fail(f"{label} must contain a JSON object")
        return None
    return document


def _verify_file_record(
    result: VerificationResult,
    workspace_root: Path,
    record: object,
    label: str,
    *,
    expected_path: str | None = None,
) -> Path | None:
    """Validate a root-confined path, byte count, and SHA-256 record."""
    if not isinstance(record, dict):
        result.fail(f"{label} record must be an object")
        return None

    raw_path = record.get("path")
    if expected_path is not None and raw_path != expected_path:
        result.fail(f"{label} path must be {expected_path!r}, got {raw_path!r}")
    path = _root_confined_path(result, workspace_root, raw_path, label)
    if path is None:
        return None
    if not path.is_file():
        result.fail(f"{label} file is missing: {raw_path}")
        return None

    expected_bytes = record.get("bytes")
    if not isinstance(expected_bytes, int) or expected_bytes < 0:
        result.fail(f"{label} byte count must be a non-negative integer")
    elif path.stat().st_size != expected_bytes:
        result.fail(
            f"{label} byte count mismatch: {path.stat().st_size} != {expected_bytes}"
        )

    expected_sha256 = record.get("sha256")
    if (
        not isinstance(expected_sha256, str)
        or SHA256_PATTERN.fullmatch(expected_sha256) is None
    ):
        result.fail(f"{label} SHA-256 must be 64 lowercase hex characters")
    elif file_sha256(path) != expected_sha256:
        result.fail(f"{label} SHA-256 mismatch: {raw_path}")
    return path


def _parse_sha_manifest(
    result: VerificationResult,
    manifest_root: Path,
    manifest_path: Path,
    label: str,
    *,
    expected_records: int | None,
    require_read_only: bool,
) -> dict[str, str]:
    """Verify a GNU-style SHA manifest and every root-confined record."""
    try:
        lines = manifest_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        result.fail(f"{label} cannot be read: {error}")
        return {}
    if expected_records is not None and len(lines) != expected_records:
        result.fail(
            f"{label} must contain {expected_records} records, got {len(lines)}"
        )
    if not lines:
        result.fail(f"{label} must not be empty")
        return {}

    records: dict[str, str] = {}
    for record_index, line in enumerate(lines, start=1):
        match = re.fullmatch(r"([0-9a-f]{64})  \./(.+)", line)
        if match is None:
            result.fail(f"{label} record {record_index} has invalid shape")
            continue
        expected_sha256, relative_text = match.groups()
        if relative_text in records:
            result.fail(f"{label} contains duplicate path: {relative_text}")
            continue
        path = _root_confined_path(
            result,
            manifest_root,
            relative_text,
            f"{label} record {record_index}",
        )
        if path is None:
            continue
        if not path.is_file():
            result.fail(f"{label} record file is missing: {relative_text}")
            continue
        if file_sha256(path) != expected_sha256:
            result.fail(f"{label} record SHA-256 mismatch: {relative_text}")
        if require_read_only and path.stat().st_mode & 0o222:
            result.fail(f"{label} record remains writable: {relative_text}")
        records[relative_text] = expected_sha256
    return records


def _parse_unique_key_values(
    result: VerificationResult,
    path: Path,
    label: str,
) -> dict[str, str]:
    """Parse anchored key/value evidence while rejecting duplicate keys."""
    pairs: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        result.fail(f"{label} cannot be read: {error}")
        return pairs
    for line_number, line in enumerate(lines, start=1):
        if "=" not in line:
            result.fail(f"{label} line {line_number} is not key=value")
            continue
        key, value = line.split("=", 1)
        if not key or key in pairs:
            result.fail(f"{label} has an empty or duplicate key: {key!r}")
            continue
        pairs[key] = value
    return pairs


def _verify_attestation_scope(
    result: VerificationResult,
    attestation: dict[str, Any],
) -> None:
    """Require the narrow verdict and an explicitly unevaluated corpus-on arm."""
    if attestation.get("schema_version") != ATTESTATION_SCHEMA_VERSION:
        result.fail("attestation schema version is invalid")
    if attestation.get("verdict") != "PASS_HYBRID_FLAG_OFF":
        result.fail("attestation verdict must be PASS_HYBRID_FLAG_OFF")
    if attestation.get("claim_scope") != CLAIM_SCOPE:
        result.fail(f"attestation claim scope must be {CLAIM_SCOPE!r}")
    if attestation.get("freeze_sha") != FREEZE_SHA:
        result.fail(f"attestation freeze SHA must be {FREEZE_SHA}")
    if attestation.get("full_campaign_status") != "NOT_EVALUATED":
        result.fail("full campaign status must be NOT_EVALUATED")
    if attestation.get("promotion_authorized") is not False:
        result.fail("promotion_authorized must be false")

    corpus_on = attestation.get("corpus_on")
    if not isinstance(corpus_on, dict):
        result.fail("corpus-on state must be an object")
        return
    if corpus_on.get("status") != "NOT_EVALUATED":
        result.fail("corpus-on status must be NOT_EVALUATED")
    if corpus_on.get("physical_correction_calls") != 0:
        result.fail("corpus-on physical correction calls must remain 0")
    if corpus_on.get("authorized") is not False:
        result.fail("corpus-on authorized must be false")


def _verify_packet_manifest(
    result: VerificationResult,
    workspace_root: Path,
    attestation_path: Path,
    attestation: dict[str, Any],
    *,
    require_packet_manifest: bool,
    require_read_only: bool,
) -> None:
    """Verify the recovery packet's own sealed artifact manifest."""
    if not require_packet_manifest:
        return
    manifest_path = _root_confined_path(
        result,
        workspace_root,
        attestation.get("packet_manifest_path"),
        "packet manifest",
    )
    if manifest_path is None:
        return
    if manifest_path.parent != attestation_path.parent:
        result.fail("packet manifest must live beside the attestation")
        return
    if not manifest_path.is_file():
        result.fail("packet manifest file is missing")
        return
    if require_read_only:
        if manifest_path.stat().st_mode & 0o222:
            result.fail("packet manifest remains writable")
        if manifest_path.parent.stat().st_mode & 0o222:
            result.fail("packet root remains writable")

    records = _parse_sha_manifest(
        result,
        manifest_path.parent,
        manifest_path,
        "packet manifest",
        expected_records=None,
        require_read_only=require_read_only,
    )
    required_names = {
        "attestation.json",
        "decision.md",
        "recovery-source-manifest.tsv",
        "future-cap-packet.json",
    }
    if not required_names.issubset(records):
        missing = sorted(required_names - records.keys())
        result.fail(f"packet manifest is missing required records: {missing}")


def _verify_recovery_source_manifest(
    result: VerificationResult,
    workspace_root: Path,
    attestation: dict[str, Any],
) -> dict[str, str]:
    """Require exactly the three recovery sources and three focused tests."""
    manifest_path = _verify_file_record(
        result,
        workspace_root,
        attestation.get("recovery_source_manifest"),
        "recovery source manifest",
    )
    if manifest_path is None:
        return {}
    try:
        lines = manifest_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        result.fail(f"recovery source manifest cannot be read: {error}")
        return {}
    if not lines or lines[0] != "path\tbytes\tsha256":
        result.fail("recovery source manifest header must be path<TAB>bytes<TAB>sha256")
        return {}
    if len(lines) != len(RECOVERY_SOURCE_PATHS) + 1:
        result.fail(
            "recovery source manifest must contain exactly six records "
            f"(got {max(0, len(lines) - 1)})"
        )

    records: dict[str, str] = {}
    observed_paths: list[str] = []
    for record_index, line in enumerate(lines[1:], start=1):
        fields = line.split("\t")
        if len(fields) != 3:
            result.fail(
                f"recovery source manifest record {record_index} has invalid shape"
            )
            continue
        raw_path, raw_bytes, expected_sha256 = fields
        observed_paths.append(raw_path)
        if raw_path in records:
            result.fail(f"recovery source manifest duplicates path: {raw_path}")
            continue
        if not raw_bytes.isdigit():
            result.fail(f"recovery source byte count must be decimal: {raw_path!r}")
            continue
        if SHA256_PATTERN.fullmatch(expected_sha256) is None:
            result.fail(
                f"recovery source SHA-256 must be 64 lowercase hex: {raw_path!r}"
            )
            continue
        path = _root_confined_path(
            result,
            workspace_root,
            raw_path,
            f"recovery source manifest record {record_index}",
        )
        if path is None:
            continue
        if not path.is_file():
            result.fail(f"recovery source file is missing: {raw_path}")
            continue
        if path.stat().st_size != int(raw_bytes):
            result.fail(f"recovery source byte count mismatch: {raw_path}")
        if file_sha256(path) != expected_sha256:
            result.fail(f"recovery source SHA-256 mismatch: {raw_path}")
        records[raw_path] = expected_sha256

    if tuple(observed_paths) != RECOVERY_SOURCE_PATHS:
        result.fail(
            "recovery source paths/order must match the approved six-file delta"
        )
    result.checks["recovery_source_records"] = len(records)
    return records


def _verify_historical_identity_receipts(
    result: VerificationResult,
    workspace_root: Path,
    attestation: dict[str, Any],
) -> None:
    """Bind each inherited arm to its freeze/HEAD/clean-surface receipt."""
    raw_receipts = attestation.get("historical_identity_receipts")
    if not isinstance(raw_receipts, list):
        result.fail("historical identity receipts must be a list")
        return
    observed_arms = [
        receipt.get("arm") if isinstance(receipt, dict) else None
        for receipt in raw_receipts
    ]
    if tuple(observed_arms) != HISTORICAL_IDENTITY_ARMS:
        result.fail(
            "historical identity receipts must contain targets-off, "
            "targets-on, and corpus-off in order"
        )

    for receipt in raw_receipts:
        if not isinstance(receipt, dict):
            continue
        arm = receipt.get("arm")
        if arm not in HISTORICAL_IDENTITY_ARMS:
            continue
        expected_path = (
            f"{LEGACY_ACCEPTANCE_ROOT.as_posix()}/arms/{arm}/identity-check.txt"
        )
        path = _verify_file_record(
            result,
            workspace_root,
            receipt,
            f"historical identity receipt {arm}",
            expected_path=expected_path,
        )
        if path is None:
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        required_prefixes = {
            f"arm={arm} checked_at=": False,
            f"freeze_sha={FREEZE_SHA}": False,
            f"head_sha={FREEZE_SHA}": False,
            "candidate-surface: clean": False,
            "development-corpus: valid": False,
        }
        for expected_line in required_prefixes:
            if expected_line.endswith("="):
                required_prefixes[expected_line] = any(
                    line.startswith(expected_line) for line in lines
                )
            elif expected_line.startswith("arm="):
                required_prefixes[expected_line] = any(
                    line.startswith(expected_line) for line in lines
                )
            else:
                required_prefixes[expected_line] = lines.count(expected_line) == 1
        for expected_line, present in required_prefixes.items():
            if not present:
                result.fail(
                    f"historical identity receipt {arm} lacks {expected_line!r}"
                )
    result.checks["historical_identity_receipts"] = len(raw_receipts)


def _verify_historical_corpus_receipts(
    result: VerificationResult,
    workspace_root: Path,
    attestation: dict[str, Any],
) -> None:
    """Bind the nine ready historical corpus-off responses replaced around d2c09."""
    expected_fixtures = tuple(
        fixture for fixture in CORPUS_CHUNK_VECTOR if fixture != REPLACEMENT_FIXTURE
    )
    raw_receipts = attestation.get("historical_corpus_off_receipts")
    if not isinstance(raw_receipts, list):
        result.fail("historical corpus-off receipts must be a list")
        return
    observed_fixtures = [
        receipt.get("fixture") if isinstance(receipt, dict) else None
        for receipt in raw_receipts
    ]
    if tuple(observed_fixtures) != expected_fixtures:
        result.fail(
            "historical corpus-off receipts must contain exactly the nine "
            "ready non-d2c09 fixtures in manifest order"
        )

    for receipt in raw_receipts:
        if not isinstance(receipt, dict):
            continue
        fixture = receipt.get("fixture")
        if fixture not in expected_fixtures:
            continue
        expected_chunks = CORPUS_CHUNK_VECTOR[str(fixture)]
        if receipt.get("expected_chunks") != expected_chunks:
            result.fail(f"historical corpus-off expected_chunks mismatch: {fixture}")
        expected_path = (
            f"{LEGACY_ACCEPTANCE_ROOT.as_posix()}/arms/corpus-off/corpus/"
            f"{fixture}/correction-response.json"
        )
        path = _verify_file_record(
            result,
            workspace_root,
            receipt,
            f"historical corpus-off receipt {fixture}",
            expected_path=expected_path,
        )
        if path is None:
            continue
        response = _load_json(
            result,
            path,
            f"historical corpus-off response {fixture}",
        )
        if response is None:
            continue
        expected_values = {
            "status": "ready",
            "source_state": "whole_visit_corrected",
            "chunk_count": expected_chunks,
            "attempts": 1,
            "retried": False,
        }
        for key, expected_value in expected_values.items():
            if response.get(key) != expected_value:
                result.fail(
                    f"historical corpus-off {fixture} {key} must be {expected_value!r}"
                )
    result.checks["historical_corpus_off_ready_receipts"] = len(raw_receipts)


def _verify_replay_summary(
    result: VerificationResult,
    summary: dict[str, Any],
) -> None:
    """Verify the replacement replay's exact narrow outcome."""
    expected_values = {
        "status": REPLAY_STATUS,
        "fixture": REPLACEMENT_FIXTURE,
        "correction_status": "ready",
        "rediarization_flag": 0,
        "physical_correction_calls": 4,
        "replays": 1,
        "chunk_count": 3,
        "attempts": 2,
        "retried": True,
        "word_confidence_fallbacks": 1,
        "punctuation_timing_folds": 1,
        "normal_runtime_restored": True,
        "claim_scope": (
            "flag-off recovery availability only; corpus-on and promotion not evaluated"
        ),
    }
    for key, expected_value in expected_values.items():
        if summary.get(key) != expected_value:
            if key == "physical_correction_calls":
                result.fail("physical correction calls must be exactly 4")
            else:
                result.fail(
                    f"replacement replay summary {key} must be {expected_value!r}"
                )


def _verify_replay_diagnostic(
    result: VerificationResult,
    diagnostic: dict[str, Any],
) -> None:
    """Verify final replay checks, counts, scope, and post-run spend."""
    if diagnostic.get("status") != REPLAY_STATUS:
        result.fail(f"replay diagnostic status must be {REPLAY_STATUS}")
    if diagnostic.get("claim_scope") != (
        "replacement flag-off recovery replay only; corpus-on and promotion "
        "not evaluated"
    ):
        result.fail("replay diagnostic claim scope is not flag-off-only")

    checks = diagnostic.get("checks")
    required_checks = {
        "eval_exit_zero",
        "flag_off_no_rediarization",
        "normal_runtime_restored",
        "physical_call_count_four",
        "single_exact_recovery",
        "source_hashes_unchanged",
    }
    if not isinstance(checks, dict):
        result.fail("replay diagnostic checks must be an object")
    else:
        missing = sorted(required_checks - checks.keys())
        if missing:
            result.fail(f"replay diagnostic required checks are missing: {missing}")
        false_checks = sorted(key for key, value in checks.items() if value is not True)
        if false_checks:
            result.fail(f"replay diagnostic checks are not true: {false_checks}")

    counts = diagnostic.get("counts")
    expected_counts = {
        "physical_correction_calls": 4,
        "model_restores": 1,
        "word_confidence_fallbacks": 1,
        "punctuation_timing_folds": 1,
    }
    if not isinstance(counts, dict):
        result.fail("replay diagnostic counts must be an object")
    else:
        for key, expected_value in expected_counts.items():
            if counts.get(key) != expected_value:
                if key == "physical_correction_calls":
                    result.fail("physical correction calls must be exactly 4")
                else:
                    result.fail(
                        f"replay diagnostic count {key} must be {expected_value}"
                    )

    spend_after = diagnostic.get("spend_after")
    expected_spend = {
        "physical_correction_calls": "72/95",
        "replays": "31/41",
        "corpus_on_calls": 0,
    }
    if not isinstance(spend_after, dict):
        result.fail("replay diagnostic spend_after must be an object")
    else:
        for key, expected_value in expected_spend.items():
            if spend_after.get(key) != expected_value:
                result.fail(f"replay diagnostic spend {key} must be {expected_value!r}")


def _verify_call_ledger(
    result: VerificationResult,
    ledger_path: Path,
) -> None:
    """Count physical model calls instead of the API's logical chunk count."""
    ledger = _parse_unique_key_values(
        result,
        ledger_path,
        "replacement replay call ledger",
    )
    expected_values = {
        "approved_replay_cap": "1",
        "actual_replays": "1",
        "planned_chunks": "3",
        "approved_physical_correction_call_cap": "4",
        "actual_physical_correction_calls": "4",
        "model_restores": "1",
        "word_confidence_fallback_events": "1",
        "fifth_call_attempted": "false",
    }
    for key, expected_value in expected_values.items():
        if ledger.get(key) != expected_value:
            if key == "actual_physical_correction_calls":
                result.fail("physical correction calls must be exactly 4")
            else:
                result.fail(
                    f"replacement replay call ledger {key} must be {expected_value!r}"
                )
    fold_value = ledger.get("punctuation_timing_fold_events", "")
    if fold_value != "1 input_rows=214 output_words=213 folds=1":
        result.fail("replacement replay punctuation timing fold ledger is invalid")


def _verify_run_identity(
    result: VerificationResult,
    identity_path: Path,
) -> None:
    """Require the replacement replay's flag-off and zero-corpus-on identity."""
    identity = _parse_unique_key_values(
        result,
        identity_path,
        "replacement replay run identity",
    )
    expected_values = {
        "diagnostic": "d2c09-d3-recovery-only-replay1",
        "flag": "0",
        "fixture": REPLACEMENT_FIXTURE,
        "pace": "1x",
        "chunk_ms": "5000",
        "replay_cap": "1",
        "physical_correction_call_cap": "4",
        "corpus_on_cap": "0",
    }
    for key, expected_value in expected_values.items():
        if identity.get(key) != expected_value:
            result.fail(f"replacement replay identity {key} must be {expected_value!r}")


def _verify_replacement_replay(
    result: VerificationResult,
    workspace_root: Path,
    attestation: dict[str, Any],
    *,
    require_read_only: bool,
) -> None:
    """Verify the sealed replay, its manifest, and exactly four physical calls."""
    replay = attestation.get("replacement_replay")
    if not isinstance(replay, dict):
        result.fail("replacement replay binding must be an object")
        return
    if replay.get("fixture") != REPLACEMENT_FIXTURE:
        result.fail(f"replacement replay fixture must be {REPLACEMENT_FIXTURE}")
    replay_root = _root_confined_path(
        result,
        workspace_root,
        replay.get("root"),
        "replacement replay root",
    )
    if replay_root is None:
        return
    if not replay_root.is_dir():
        result.fail("replacement replay root is missing")
        return
    if require_read_only and replay_root.stat().st_mode & 0o222:
        result.fail("replacement replay root remains writable")

    manifest_record = replay.get("artifact_manifest")
    expected_manifest_path = (
        Path(str(replay.get("root"))) / "artifact-manifest.sha256"
    ).as_posix()
    manifest_path = _verify_file_record(
        result,
        workspace_root,
        manifest_record,
        "replacement replay artifact manifest",
        expected_path=expected_manifest_path,
    )
    if not isinstance(manifest_record, dict):
        return
    if manifest_record.get("records") != REPLAY_MANIFEST_RECORDS:
        result.fail(
            f"replacement replay manifest record count must be "
            f"{REPLAY_MANIFEST_RECORDS}"
        )
    if manifest_path is None:
        return
    if require_read_only and manifest_path.stat().st_mode & 0o222:
        result.fail("replacement replay artifact manifest remains writable")
    replay_manifest = _parse_sha_manifest(
        result,
        replay_root,
        manifest_path,
        "replacement replay artifact manifest",
        expected_records=REPLAY_MANIFEST_RECORDS,
        require_read_only=require_read_only,
    )

    bindings = replay.get("bindings")
    if not isinstance(bindings, list):
        result.fail("replacement replay bindings must be a list")
        return
    labels = [
        binding.get("label") if isinstance(binding, dict) else None
        for binding in bindings
    ]
    if tuple(labels) != tuple(REPLAY_BINDING_NAMES):
        result.fail(
            "replacement replay bindings must contain result_summary, "
            "diagnostic_verification, call_ledger, and run_identity in order"
        )

    bound_paths: dict[str, Path] = {}
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        label = binding.get("label")
        if label not in REPLAY_BINDING_NAMES:
            continue
        expected_path = (
            (replay_root / REPLAY_BINDING_NAMES[str(label)])
            .relative_to(workspace_root.resolve())
            .as_posix()
        )
        path = _verify_file_record(
            result,
            workspace_root,
            binding,
            "replay binding",
            expected_path=expected_path,
        )
        if path is None:
            continue
        replay_relative = path.relative_to(replay_root).as_posix()
        if replay_relative not in replay_manifest:
            result.fail(f"replay binding is absent from artifact manifest: {label}")
        bound_paths[str(label)] = path

    summary_path = bound_paths.get("result_summary")
    if summary_path is not None:
        summary = _load_json(result, summary_path, "replacement replay summary")
        if summary is not None:
            _verify_replay_summary(result, summary)
    diagnostic_path = bound_paths.get("diagnostic_verification")
    if diagnostic_path is not None:
        diagnostic = _load_json(
            result,
            diagnostic_path,
            "replacement replay diagnostic",
        )
        if diagnostic is not None:
            _verify_replay_diagnostic(result, diagnostic)
    ledger_path = bound_paths.get("call_ledger")
    if ledger_path is not None:
        _verify_call_ledger(result, ledger_path)
    identity_path = bound_paths.get("run_identity")
    if identity_path is not None:
        _verify_run_identity(result, identity_path)
    result.checks["replacement_replay_manifest_records"] = len(replay_manifest)


def _verify_cap_packet(
    result: VerificationResult,
    workspace_root: Path,
    attestation: dict[str, Any],
    recovery_source_hashes: dict[str, str],
) -> None:
    """Verify executable future-arm arithmetic without granting authorization."""
    cap_path = _verify_file_record(
        result,
        workspace_root,
        attestation.get("future_cap_packet"),
        "future cap packet",
    )
    if cap_path is None:
        return
    cap_packet = _load_json(result, cap_path, "future cap packet")
    if cap_packet is None:
        return
    if cap_packet.get("schema_version") != CAP_SCHEMA_VERSION:
        result.fail("future cap packet schema version is invalid")
    if cap_packet.get("corpus_on_status") != "NOT_EVALUATED":
        result.fail("future cap packet corpus-on status must be NOT_EVALUATED")
    if cap_packet.get("authorization") != "NOT_AUTHORIZED":
        result.fail("future cap packet authorization must be NOT_AUTHORIZED")
    if cap_packet.get("requires_new_approval") is not True:
        result.fail("future cap packet must require new approval")

    chunker_path = "strands_agents/post_visit_correction.py"
    production_chunker = _verify_file_record(
        result,
        workspace_root,
        cap_packet.get("production_chunker"),
        "production chunker",
        expected_path=chunker_path,
    )
    if production_chunker is not None and recovery_source_hashes.get(
        chunker_path
    ) != file_sha256(production_chunker):
        result.fail("future cap packet chunker hash is not recovery-source-bound")
    _verify_file_record(
        result,
        workspace_root,
        cap_packet.get("development_manifest"),
        "future cap development manifest",
        expected_path=("tests/fixtures/audio/development-corpus-0.5.0.json"),
    )

    calculation = cap_packet.get("calculation")
    expected_calculation = {
        "engine": "strands_agents.post_visit_correction._build_audio_chunks",
        "one_shot_max_audio_seconds": 240.0,
        "audio_chunk_seconds": 180.0,
        "minimum_final_chunk_seconds": 10.0,
        "synthetic_sample_rate_hz": 100,
    }
    if not isinstance(calculation, dict):
        result.fail("future cap calculation must be an object")
    else:
        for key, expected_value in expected_calculation.items():
            if calculation.get(key) != expected_value:
                result.fail(f"future cap calculation {key} must be {expected_value!r}")

    fixture_records = cap_packet.get("fixtures")
    if not isinstance(fixture_records, list):
        result.fail("future cap fixtures must be a list")
        fixture_records = []
    observed_fixtures: list[str | None] = []
    base_calls = 0
    for fixture_record in fixture_records:
        if not isinstance(fixture_record, dict):
            result.fail("future cap fixture record must be an object")
            continue
        fixture = fixture_record.get("fixture")
        observed_fixtures.append(fixture if isinstance(fixture, str) else None)
        if fixture not in CORPUS_CHUNK_VECTOR:
            result.fail(f"future cap fixture is not approved: {fixture!r}")
            continue
        duration = fixture_record.get("duration_seconds")
        if not isinstance(duration, (int, float)) or duration <= 0:
            result.fail(f"future cap duration is invalid: {fixture}")
        chunks = fixture_record.get("production_chunks")
        expected_chunks = CORPUS_CHUNK_VECTOR[fixture]
        if chunks != expected_chunks:
            result.fail(
                f"future cap production chunk count for {fixture} must be "
                f"{expected_chunks}"
            )
        else:
            base_calls += expected_chunks
    if tuple(observed_fixtures) != tuple(CORPUS_CHUNK_VECTOR):
        result.fail("future cap fixtures must match the ten-case manifest order")

    expected_values = {
        "base_transcribe_calls": base_calls,
        "expected_recovery_calls": 1,
        "conservative_recovery_calls": len(CORPUS_CHUNK_VECTOR),
        "current_physical_calls": 72,
        "existing_approved_call_cap": 95,
        "expected_future_arm_calls": base_calls + 1,
        "conservative_future_arm_calls": (base_calls + len(CORPUS_CHUNK_VECTOR)),
        "expected_total_physical_calls": 72 + base_calls + 1,
        "conservative_total_physical_calls": (
            72 + base_calls + len(CORPUS_CHUNK_VECTOR)
        ),
    }
    for key, expected_value in expected_values.items():
        if cap_packet.get(key) != expected_value:
            result.fail(
                f"future cap packet {key} must be {expected_value}, "
                f"got {cap_packet.get(key)!r}"
            )
    result.checks["future_base_transcribe_calls"] = base_calls
    result.checks["future_expected_total_physical_calls"] = 72 + base_calls + 1
    result.checks["future_conservative_total_physical_calls"] = (
        72 + base_calls + len(CORPUS_CHUNK_VECTOR)
    )


def _verify_bound_tooling_and_decision(
    result: VerificationResult,
    workspace_root: Path,
    attestation: dict[str, Any],
) -> None:
    """Bind verifier sources and reject broader language in the decision."""
    _verify_file_record(
        result,
        workspace_root,
        attestation.get("flag_off_recovery_verifier"),
        "flag-off recovery verifier",
        expected_path="scripts/verify-rediarization-flag-off-recovery.py",
    )
    _verify_file_record(
        result,
        workspace_root,
        attestation.get("legacy_full_verifier"),
        "legacy full verifier",
        expected_path=(f"{LEGACY_ACCEPTANCE_ROOT.as_posix()}/verify-campaign.py"),
    )
    decision_path = _verify_file_record(
        result,
        workspace_root,
        attestation.get("decision"),
        "flag-off recovery decision",
    )
    if decision_path is None:
        return
    decision = decision_path.read_text(encoding="utf-8")
    required_statements = (
        "Verdict: PASS_HYBRID_FLAG_OFF",
        "Claim: flag-off recovery availability/correctness only.",
        "Corpus-on: NOT_EVALUATED.",
        "Full campaign: NOT_EVALUATED.",
        "Promotion/default flip: NOT_AUTHORIZED.",
    )
    for statement in required_statements:
        if decision.count(statement) != 1:
            result.fail(
                f"flag-off recovery decision must contain exactly one {statement!r}"
            )


def verify_flag_off_recovery_attestation(
    workspace_root: Path,
    attestation_path: Path,
    *,
    require_packet_manifest: bool = True,
    require_read_only: bool = True,
) -> VerificationResult:
    """Verify one recovery packet against a workspace without mutation."""
    result = VerificationResult()
    resolved_root = workspace_root.resolve()
    resolved_attestation = attestation_path.resolve()
    try:
        resolved_attestation.relative_to(resolved_root)
    except ValueError:
        result.fail("attestation path escapes the workspace root")
        return result.finish()
    if not resolved_attestation.is_file():
        result.fail("attestation file is missing")
        return result.finish()

    attestation = _load_json(
        result,
        resolved_attestation,
        "flag-off recovery attestation",
    )
    if attestation is None:
        return result.finish()

    _verify_attestation_scope(result, attestation)
    _verify_packet_manifest(
        result,
        resolved_root,
        resolved_attestation,
        attestation,
        require_packet_manifest=require_packet_manifest,
        require_read_only=require_read_only,
    )
    recovery_source_hashes = _verify_recovery_source_manifest(
        result,
        resolved_root,
        attestation,
    )
    _verify_historical_identity_receipts(
        result,
        resolved_root,
        attestation,
    )
    _verify_historical_corpus_receipts(
        result,
        resolved_root,
        attestation,
    )
    _verify_replacement_replay(
        result,
        resolved_root,
        attestation,
        require_read_only=require_read_only,
    )
    _verify_cap_packet(
        result,
        resolved_root,
        attestation,
        recovery_source_hashes,
    )
    _verify_bound_tooling_and_decision(
        result,
        resolved_root,
        attestation,
    )
    result.checks["corpus_on_status"] = "NOT_EVALUATED"
    return result.finish()


def _record_for_path(workspace_root: Path, path: Path) -> dict[str, object]:
    """Create one deterministic root-relative path/size/hash record."""
    return {
        "path": path.relative_to(workspace_root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": file_sha256(path),
    }


def _write_json(path: Path, document: Any) -> None:
    """Write stable evidence JSON."""
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _wav_duration_seconds(path: Path) -> float:
    """Read only the WAV header fields required for cap generation."""
    with wave.open(str(path), "rb") as audio:
        return audio.getnframes() / float(audio.getframerate())


def _load_production_chunker(workspace_root: Path) -> Any:
    """Import the product chunker without loading a model or touching the GPU."""
    agent_root = workspace_root / "strands_agents"
    sys.path.insert(0, str(agent_root))
    try:
        return importlib.import_module("post_visit_correction")
    finally:
        sys.path.pop(0)


def _production_chunk_counts(
    workspace_root: Path,
    durations: dict[str, float],
) -> tuple[dict[str, int], dict[str, float]]:
    """Execute the production chunker on zero-filled mono duration surrogates."""
    correction_module = _load_production_chunker(workspace_root)
    constants = {
        "one_shot_max_audio_seconds": float(
            correction_module._ONE_SHOT_MAX_AUDIO_SECONDS
        ),
        "audio_chunk_seconds": float(correction_module._AUDIO_CHUNK_SECONDS),
        "minimum_final_chunk_seconds": float(
            correction_module._MIN_FINAL_CHUNK_SECONDS
        ),
    }
    sample_rate = 100
    counts: dict[str, int] = {}
    with tempfile.TemporaryDirectory(
        prefix="rediarization-recovery-cap-"
    ) as temporary_root:
        temporary_path = Path(temporary_root)
        for fixture, duration in durations.items():
            surrogate_path = temporary_path / f"{fixture}.wav"
            frame_count = round(duration * sample_rate)
            with wave.open(str(surrogate_path), "wb") as surrogate:
                surrogate.setnchannels(1)
                surrogate.setsampwidth(2)
                surrogate.setframerate(sample_rate)
                surrogate.writeframes(b"\0\0" * frame_count)
            chunks = correction_module._build_audio_chunks(surrogate_path)
            counts[fixture] = len(chunks)
            for chunk in chunks:
                if chunk.delete_after_use:
                    chunk.path.unlink(missing_ok=True)
    return counts, constants


def _build_future_cap_packet(
    workspace_root: Path,
) -> dict[str, Any]:
    """Derive the future corpus-on packet through the production chunker."""
    durations: dict[str, float] = {}
    for fixture in CORPUS_CHUNK_VECTOR:
        audio_path = workspace_root / "tests/fixtures/audio" / f"{fixture}.wav"
        if not audio_path.is_file():
            raise ValueError(f"cap input WAV is missing: {audio_path}")
        durations[fixture] = _wav_duration_seconds(audio_path)
    chunk_counts, chunker_constants = _production_chunk_counts(
        workspace_root,
        durations,
    )
    if chunk_counts != CORPUS_CHUNK_VECTOR:
        raise ValueError(
            "production chunk vector differs from the approved recovery vector: "
            f"{chunk_counts}"
        )

    base_calls = sum(chunk_counts.values())
    expected_recovery_calls = 1
    conservative_recovery_calls = len(chunk_counts)
    current_physical_calls = 72
    development_manifest = (
        workspace_root / "tests/fixtures/audio/development-corpus-0.5.0.json"
    )
    chunker_path = workspace_root / "strands_agents/post_visit_correction.py"
    return {
        "authorization": "NOT_AUTHORIZED",
        "base_transcribe_calls": base_calls,
        "calculation": {
            "engine": ("strands_agents.post_visit_correction._build_audio_chunks"),
            **chunker_constants,
            "synthetic_sample_rate_hz": 100,
        },
        "conservative_future_arm_calls": (base_calls + conservative_recovery_calls),
        "conservative_recovery_calls": conservative_recovery_calls,
        "conservative_total_physical_calls": (
            current_physical_calls + base_calls + conservative_recovery_calls
        ),
        "corpus_on_status": "NOT_EVALUATED",
        "current_physical_calls": current_physical_calls,
        "development_manifest": _record_for_path(
            workspace_root,
            development_manifest,
        ),
        "existing_approved_call_cap": 95,
        "expected_future_arm_calls": base_calls + expected_recovery_calls,
        "expected_recovery_calls": expected_recovery_calls,
        "expected_total_physical_calls": (
            current_physical_calls + base_calls + expected_recovery_calls
        ),
        "fixtures": [
            {
                "fixture": fixture,
                "duration_seconds": round(durations[fixture], 6),
                "production_chunks": chunk_counts[fixture],
            }
            for fixture in CORPUS_CHUNK_VECTOR
        ],
        "production_chunker": _record_for_path(
            workspace_root,
            chunker_path,
        ),
        "requires_new_approval": True,
        "schema_version": CAP_SCHEMA_VERSION,
    }


def _write_recovery_source_manifest(
    workspace_root: Path,
    manifest_path: Path,
) -> None:
    """Write the exact six-record recovery source manifest."""
    lines = ["path\tbytes\tsha256"]
    for raw_path in RECOVERY_SOURCE_PATHS:
        path = workspace_root / raw_path
        if not path.is_file():
            raise ValueError(f"recovery source file is missing: {raw_path}")
        lines.append(f"{raw_path}\t{path.stat().st_size}\t{file_sha256(path)}")
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _historical_receipts(workspace_root: Path) -> list[dict[str, object]]:
    """Bind all three historical identity receipt files."""
    receipts: list[dict[str, object]] = []
    for arm in HISTORICAL_IDENTITY_ARMS:
        path = (
            workspace_root
            / LEGACY_ACCEPTANCE_ROOT
            / "arms"
            / arm
            / "identity-check.txt"
        )
        receipts.append(
            {
                "arm": arm,
                **_record_for_path(workspace_root, path),
            }
        )
    return receipts


def _historical_corpus_receipts(
    workspace_root: Path,
) -> list[dict[str, object]]:
    """Bind the nine historical ready corpus-off responses."""
    receipts: list[dict[str, object]] = []
    for fixture, expected_chunks in CORPUS_CHUNK_VECTOR.items():
        if fixture == REPLACEMENT_FIXTURE:
            continue
        path = (
            workspace_root
            / LEGACY_ACCEPTANCE_ROOT
            / "arms/corpus-off/corpus"
            / fixture
            / "correction-response.json"
        )
        receipts.append(
            {
                "expected_chunks": expected_chunks,
                "fixture": fixture,
                **_record_for_path(workspace_root, path),
            }
        )
    return receipts


def _replacement_replay_binding(
    workspace_root: Path,
    replay_root: Path,
) -> dict[str, Any]:
    """Bind the sealed replay manifest and four sanitized proof files."""
    manifest_path = replay_root / "artifact-manifest.sha256"
    return {
        "artifact_manifest": {
            "records": REPLAY_MANIFEST_RECORDS,
            **_record_for_path(workspace_root, manifest_path),
        },
        "bindings": [
            {
                "label": label,
                **_record_for_path(workspace_root, replay_root / filename),
            }
            for label, filename in REPLAY_BINDING_NAMES.items()
        ],
        "fixture": REPLACEMENT_FIXTURE,
        "root": replay_root.relative_to(workspace_root).as_posix(),
    }


def _write_packet_manifest(
    packet_root: Path,
    paths: list[Path],
) -> Path:
    """Write a root-confined GNU SHA manifest over packet artifacts."""
    manifest_path = packet_root / "packet-manifest.sha256"
    lines = [
        f"{file_sha256(path)}  ./{path.relative_to(packet_root).as_posix()}"
        for path in sorted(paths)
    ]
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest_path


def build_flag_off_recovery_packet(
    workspace_root: Path,
    packet_root: Path,
    replay_root: Path,
) -> VerificationResult:
    """Create, pre-verify, manifest, and seal a recovery evidence packet."""
    resolved_root = workspace_root.resolve()
    resolved_packet = (
        packet_root if packet_root.is_absolute() else resolved_root / packet_root
    ).resolve()
    resolved_replay = (
        replay_root if replay_root.is_absolute() else resolved_root / replay_root
    ).resolve()
    try:
        resolved_packet.relative_to(resolved_root)
        resolved_replay.relative_to(resolved_root)
    except ValueError as error:
        result = VerificationResult()
        result.fail(f"build path escapes workspace root: {error}")
        return result.finish()
    if resolved_packet.exists():
        result = VerificationResult()
        result.fail(f"packet root already exists: {resolved_packet}")
        return result.finish()
    if not resolved_replay.is_dir():
        result = VerificationResult()
        result.fail(f"replacement replay root is missing: {resolved_replay}")
        return result.finish()

    resolved_packet.mkdir(parents=True)
    try:
        recovery_source_manifest_path = resolved_packet / "recovery-source-manifest.tsv"
        _write_recovery_source_manifest(
            resolved_root,
            recovery_source_manifest_path,
        )

        cap_packet_path = resolved_packet / "future-cap-packet.json"
        _write_json(
            cap_packet_path,
            _build_future_cap_packet(resolved_root),
        )

        decision_path = resolved_packet / "decision.md"
        decision_path.write_text(
            "\n".join(
                [
                    "# Flag-off recovery decision",
                    "",
                    "Verdict: PASS_HYBRID_FLAG_OFF",
                    "Claim: flag-off recovery availability/correctness only.",
                    "Corpus-on: NOT_EVALUATED.",
                    "Full campaign: NOT_EVALUATED.",
                    "Promotion/default flip: NOT_AUTHORIZED.",
                    "",
                    "The historical target arms and nine ready corpus-off "
                    "fixtures retain their frozen",
                    "identity receipts. The amended d2c09 replay replaces only "
                    "the unavailable flag-off",
                    "result and records four physical calls for three logical "
                    "chunks. This packet does",
                    "not reinterpret the original full verifier and does not "
                    "authorize numeric cap",
                    "expansion, corpus-on execution, a default flip, or ADR "
                    "acceptance.",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        attestation_path = resolved_packet / "attestation.json"
        packet_manifest_path = resolved_packet / "packet-manifest.sha256"
        attestation = {
            "claim_scope": CLAIM_SCOPE,
            "corpus_on": {
                "authorized": False,
                "physical_correction_calls": 0,
                "status": "NOT_EVALUATED",
            },
            "recovery_source_manifest": _record_for_path(
                resolved_root,
                recovery_source_manifest_path,
            ),
            "decision": _record_for_path(resolved_root, decision_path),
            "freeze_sha": FREEZE_SHA,
            "full_campaign_status": "NOT_EVALUATED",
            "future_cap_packet": _record_for_path(
                resolved_root,
                cap_packet_path,
            ),
            "historical_corpus_off_receipts": (
                _historical_corpus_receipts(resolved_root)
            ),
            "historical_identity_receipts": _historical_receipts(resolved_root),
            "legacy_full_verifier": _record_for_path(
                resolved_root,
                (resolved_root / LEGACY_ACCEPTANCE_ROOT / "verify-campaign.py"),
            ),
            "packet_manifest_path": packet_manifest_path.relative_to(
                resolved_root
            ).as_posix(),
            "promotion_authorized": False,
            "replacement_replay": _replacement_replay_binding(
                resolved_root,
                resolved_replay,
            ),
            "schema_version": ATTESTATION_SCHEMA_VERSION,
            "flag_off_recovery_verifier": _record_for_path(
                resolved_root,
                (resolved_root / "scripts/verify-rediarization-flag-off-recovery.py"),
            ),
            "verdict": "PASS_HYBRID_FLAG_OFF",
        }
        _write_json(attestation_path, attestation)

        preseal_result = verify_flag_off_recovery_attestation(
            resolved_root,
            attestation_path,
            require_packet_manifest=False,
            require_read_only=False,
        )
        if not preseal_result.ok:
            return preseal_result
        preseal_path = resolved_packet / "flag-off-recovery-verifier-preseal.json"
        _write_json(preseal_path, preseal_result.as_document())

        manifest_path = _write_packet_manifest(
            resolved_packet,
            [
                attestation_path,
                cap_packet_path,
                recovery_source_manifest_path,
                decision_path,
                preseal_path,
            ],
        )
        for packet_path in resolved_packet.iterdir():
            packet_path.chmod(0o400 if packet_path == manifest_path else 0o444)
        resolved_packet.chmod(0o555)
    except (ImportError, OSError, ValueError) as error:
        result = VerificationResult()
        result.fail(f"flag-off recovery packet build failed: {error}")
        return result.finish()

    return verify_flag_off_recovery_attestation(
        resolved_root,
        attestation_path,
    )


def _parser() -> argparse.ArgumentParser:
    """Create the build/verify CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="controlling Ambient Scribe workspace",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    verify_parser = subparsers.add_parser(
        "verify",
        help="verify an existing sealed flag-off recovery packet",
    )
    verify_parser.add_argument("--attestation", type=Path, required=True)

    build_parser = subparsers.add_parser(
        "build",
        help="build and seal a new flag-off recovery packet",
    )
    build_parser.add_argument(
        "--packet-root",
        type=Path,
        default=DEFAULT_PACKET_ROOT,
    )
    build_parser.add_argument(
        "--replay-root",
        type=Path,
        default=DEFAULT_REPLAY_ROOT,
    )
    return parser


def main() -> int:
    """Run one recovery build or verification without broadening scope."""
    arguments = _parser().parse_args()
    workspace_root = arguments.repo_root.resolve()
    if arguments.command == "build":
        result = build_flag_off_recovery_packet(
            workspace_root,
            arguments.packet_root,
            arguments.replay_root,
        )
    else:
        attestation_path = arguments.attestation
        if not attestation_path.is_absolute():
            attestation_path = workspace_root / attestation_path
        result = verify_flag_off_recovery_attestation(
            workspace_root,
            attestation_path,
        )
    print(json.dumps(result.as_document(), indent=2, sort_keys=True))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
