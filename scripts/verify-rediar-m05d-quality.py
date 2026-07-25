#!/usr/bin/env python3
"""Build and verify the CPU-only M05D corpus-quality adjudication packet.

The adjudicator consumes only existing sealed artifacts. It never calls the
application runtime, GPU, model provider, correction API, or role agent, and
it cannot emit a full-campaign pass or authorize promotion.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE_ROOT = Path("var/quality/rediar-m05-acceptance")
CORPUS_OFF_ROOT = ACCEPTANCE_ROOT / "arms/corpus-off"
CORPUS_ON_ROOT = ACCEPTANCE_ROOT / "arms/corpus-on"
M05B_PACKET_ROOT = ACCEPTANCE_ROOT / "hybrid/2026-07-25_d3-flag-off-promotion2"
M05B_VERIFICATION_ROOT = (
    ACCEPTANCE_ROOT / "hybrid/2026-07-25_d3-flag-off-promotion2-verification"
)
DEFAULT_PACKET_ROOT = ACCEPTANCE_ROOT / "adjudication/2026-07-25_m05d-quality1"

LEGACY_ADJUDICATION_PATH = CORPUS_ON_ROOT / "legacy-full-verifier-adjudication.json"
SOURCE_ADJUDICATION_PATH = CORPUS_ON_ROOT / "source-chip-findings-adjudication.json"
ARM_VERIFICATION_PATH = CORPUS_ON_ROOT / "arm-verification.json"
LEGACY_VERIFIER_PATH = ACCEPTANCE_ROOT / "verify-campaign.py"
M05B_ATTESTATION_PATH = M05B_PACKET_ROOT / "attestation.json"
M05B_VERIFIER_PATH = Path("scripts/verify-rediar-m05-hybrid.py")

C03_FIXTURE = "primock57-day1-consultation03-i-have-terrible-headache"
C08_FIXTURE = "primock57-day1-consultation08-i-have-dry-itchy-skin"
D2C09_FIXTURE = "primock57-day2-consultation09-i-cant-move-my-left-arm"

DECISION_SCHEMA_VERSION = "ambient-scribe-rediar-m05d-decision/v1"
GATE_MATRIX_SCHEMA_VERSION = "ambient-scribe-rediar-m05d-gates/v1"
SOURCE_MATRIX_SCHEMA_VERSION = "ambient-scribe-rediar-m05d-source-chip/v1"
SOURCE_IDENTITIES_SCHEMA_VERSION = "ambient-scribe-rediar-m05d-sources/v1"
RECEIPTS_SCHEMA_VERSION = "ambient-scribe-rediar-m05d-receipts/v1"
RESULT_SCHEMA_VERSION = "ambient-scribe-rediar-m05d-verifier-result/v1"

ALLOWED_DISPOSITIONS = frozenset(
    {
        "REJECT_CANDIDATE_KEEP_FLAG_OFF",
        "ELIGIBLE_FOR_SEPARATE_SUPPLEMENTAL_COMPARISON_APPROVAL",
        "BLOCKED_UNVERIFIED",
    }
)
EXPECTED_LEGACY_GATE_IDS = (
    "c03_worsened_corrected_row",
    "c03_newly_confident_wrong_corrected_row",
    "c08_corrected_strict_regression",
    "c08_incorrect_confident_regression",
    "c08_corrected_text_alignment",
    "historical_d2c09_missing_corrected_artifacts",
    "historical_d2c09_missing_completion_event",
)
EXPECTED_RECEIPT_NAMES = frozenset(
    {"ruff_check", "ruff_format", "py_compile", "pytest"}
)
EXPECTED_SOURCE_INPUT_COUNTS = {
    "ground_truth_aligned_lexical_heuristic_false_positive": 22,
    "ground_truth_confirmed_role_error": 3,
    "unscored_overlap_or_reference_gap": 4,
}

CONFIRMED_CLASSIFICATION = "CONFIRMED_SUBSTANTIVE_CANDIDATE_REGRESSION"
UNVERIFIED_CLASSIFICATIONS = frozenset(
    {
        "UNVERIFIED_COMPARISON_ALIGNMENT",
        "UNSCORABLE_REFERENCE_GAP",
    }
)
ROW_TIME_TOLERANCE = 0.06
CONFIDENT_ROLES = frozenset({"DOCTOR", "PATIENT"})
STATE_RANK = {"wrong": 0, "unresolved": 1, "correct": 2}
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
SLUG_PATTERN = re.compile(r"[A-Za-z0-9_.:/-]+")

FROZEN_SOURCE_HASHES = {
    "var/quality/rediar-m05-acceptance/run-corpus-arm.sh": (
        "983648fce21d989c591af101cddddd8b9fa763e14b3ca5321dc5dfcadca8da69"
    ),
    "var/quality/rediar-m05-acceptance/prepare-mono-fixtures.sh": (
        "af7fa70a3433cb3b88612a537ec94c3a7144217da8464b8f6c5148fe36a26b52"
    ),
    "var/quality/rediar-m05-acceptance/restore-stereo-fixtures.sh": (
        "61baf5e01d97d1e520a8f833301aed1b57dab784012bf9bd3a2f38068d693a28"
    ),
    "scripts/eval-corrected-fixtures.sh": (
        "d296ac6c626674f52ea1ba060c7a57c279fe74db06e82ecf490e02f8a1a56dda"
    ),
    "strands_agents/post_visit_correction.py": (
        "aa3d1fc40580cc942ccde1f2c71e9ce553711f08064b80a67e09ddde7935a525"
    ),
    "scripts/verify-rediar-m05-hybrid.py": (
        "7d3960e249a04fc2fa60645a8189ecd7d2e20ced4e76c13f17360922ca80ff69"
    ),
    (
        "var/quality/rediar-m05-acceptance/arms/corpus-on/verify-arm.py"
    ): "4a31865c5f6ed0388b79cdec2110e7b5e320e8699f871550698df2898de3dbed",
}

FORBIDDEN_WORDING_KEYS = frozenset(
    {
        "content",
        "decoded_text",
        "raw_text",
        "text",
        "transcript",
        "utterance",
        "word",
        "words",
    }
)
SECRET_PATTERNS = (
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"(?i)\b(?:password|secret|token)\s*="),
)

GATE_KEYS = frozenset(
    {
        "blocking_effect",
        "candidate_causality",
        "classification",
        "disposition_group",
        "disposition_trigger",
        "gate_id",
        "legacy_failure_class",
        "proof",
        "proof_class",
    }
)
SOURCE_ALERT_KEYS = frozenset(
    {
        "adjudication",
        "alert_id",
        "candidate_causality",
        "codes",
        "disposition_group",
        "fixture",
        "input_classification",
        "proof",
        "related_legacy_gate_ids",
        "row_number",
        "segment_id",
    }
)
ALLOWED_GATE_PROOF_KEYS = frozenset(
    {
        "clean_reference_rows",
        "comparison",
        "completion_event_count",
        "end",
        "expected_role",
        "first_mismatch_row_index",
        "historical_corrected_diagnostics_present",
        "historical_corrected_transcript_present",
        "legacy_reported",
        "m05b_full_campaign_status",
        "m05b_recovery_verdict",
        "off_numerator",
        "off_rate",
        "off_role",
        "off_segment_id",
        "off_state",
        "off_text_sha256",
        "on_numerator",
        "on_rate",
        "on_role",
        "on_segment_id",
        "on_state",
        "on_text_sha256",
        "row_index",
        "row_number",
        "segment_id",
        "start",
        "text_equal",
        "timing_aligned",
        "total_text_mismatch_rows",
    }
)
ALLOWED_SOURCE_PROOF_KEYS = frozenset(
    {
        "end",
        "expected_role",
        "off_role",
        "off_segment_id",
        "off_state",
        "off_text_sha256",
        "on_role",
        "on_segment_id",
        "on_state",
        "on_text_sha256",
        "start",
        "text_equal",
        "timing_aligned",
    }
)


class EvidenceError(RuntimeError):
    """Report one deterministic build-time evidence violation."""


@dataclass
class VerificationResult:
    """Collect structural failures without broadening the allowed decision."""

    failures: list[str] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)
    disposition: str = "BLOCKED_UNVERIFIED"
    promotion_authorized: bool = False
    full_campaign_pass: bool = False

    @property
    def ok(self) -> bool:
        """Return true when the packet is structurally valid."""
        return not self.failures

    def fail(self, reason: str) -> None:
        """Record each distinct fail-closed reason once."""
        if reason not in self.failures:
            self.failures.append(reason)

    def as_document(self) -> dict[str, Any]:
        """Render stable machine-readable CLI output."""
        return {
            "checks": self.checks,
            "disposition": self.disposition,
            "failures": self.failures,
            "full_campaign_pass": self.full_campaign_pass,
            "ok": self.ok,
            "promotion_authorized": self.promotion_authorized,
        }


def file_sha256(path: Path) -> str:
    """Hash a file in bounded pieces."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def text_sha256(value: object) -> str:
    """Fingerprint decoded wording without returning or persisting it."""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def write_json(path: Path, document: Any) -> None:
    """Write stable generated JSON."""
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _require(condition: bool, reason: str) -> None:
    """Raise one bounded build error when a frozen expectation is false."""
    if not condition:
        raise EvidenceError(reason)


def _root_confined_path(
    result: VerificationResult,
    workspace_root: Path,
    raw_path: object,
    label: str,
) -> Path | None:
    """Resolve one workspace path without absolute or parent traversal."""
    if not isinstance(raw_path, str) or not raw_path:
        result.fail(f"{label} path must be a non-empty relative string")
        return None
    if "\\" in raw_path:
        result.fail(f"{label} path must use POSIX separators")
        return None
    relative_path = Path(raw_path)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        result.fail(f"{label} path is not root-confined")
        return None

    resolved_root = workspace_root.resolve()
    resolved_path = (resolved_root / relative_path).resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError:
        result.fail(f"{label} path escapes the workspace root")
        return None
    return resolved_path


def _load_json(
    result: VerificationResult,
    path: Path,
    label: str,
) -> dict[str, Any] | None:
    """Load one JSON object and convert parse errors into gate failures."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        result.fail(f"{label} is not readable JSON: {error}")
        return None
    if not isinstance(document, dict):
        result.fail(f"{label} must contain a JSON object")
        return None
    return document


def _load_build_json(path: Path, label: str) -> dict[str, Any]:
    """Load a required build input without leaking its content."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EvidenceError(f"{label} is not readable JSON: {error}") from error
    if not isinstance(document, dict):
        raise EvidenceError(f"{label} must contain a JSON object")
    return document


def _verify_file_record(
    result: VerificationResult,
    workspace_root: Path,
    record: object,
    label: str,
    *,
    expected_path: str | None = None,
) -> Path | None:
    """Validate one root-confined byte-count and SHA-256 binding."""
    if not isinstance(record, dict):
        result.fail(f"{label} record must be an object")
        return None

    raw_path = record.get("path")
    if expected_path is not None and raw_path != expected_path:
        result.fail(f"{label} path must be {expected_path}")
    path = _root_confined_path(result, workspace_root, raw_path, label)
    if path is None:
        return None
    if not path.is_file():
        result.fail(f"{label} file is missing")
        return None

    expected_bytes = record.get("bytes")
    if not isinstance(expected_bytes, int) or expected_bytes < 0:
        result.fail(f"{label} byte count must be a non-negative integer")
    elif path.stat().st_size != expected_bytes:
        result.fail(f"{label} byte count mismatch")

    expected_hash = record.get("sha256")
    if (
        not isinstance(expected_hash, str)
        or SHA256_PATTERN.fullmatch(expected_hash) is None
    ):
        result.fail(f"{label} SHA-256 must be 64 lowercase hex characters")
    elif file_sha256(path) != expected_hash:
        result.fail(f"{label} SHA-256 mismatch")
    return path


def _has_write_bits(path: Path) -> bool:
    """Return whether any Unix write bit is present."""
    return bool(path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def _verify_read_only_tree(
    result: VerificationResult,
    packet_root: Path,
) -> None:
    """Require the sealed packet and every child to have no write bits."""
    if _has_write_bits(packet_root):
        result.fail("packet directory must be read-only")
    for path in sorted(packet_root.rglob("*")):
        if _has_write_bits(path):
            kind = "directory" if path.is_dir() else "file"
            result.fail(f"packet {kind} must be read-only")


def _parse_sha_manifest(
    result: VerificationResult,
    manifest_root: Path,
    manifest_path: Path,
    label: str,
    *,
    expected_records: int | None,
) -> dict[str, str]:
    """Validate a GNU SHA-256 manifest without including itself."""
    try:
        lines = manifest_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        result.fail(f"{label} is not readable: {error}")
        return {}

    if expected_records is not None and len(lines) != expected_records:
        result.fail(
            f"{label} record count mismatch: {len(lines)} != {expected_records}"
        )

    records: dict[str, str] = {}
    for line_number, line in enumerate(lines, start=1):
        match = re.fullmatch(r"([0-9a-f]{64})  \./(.+)", line)
        if match is None:
            result.fail(f"{label} record {line_number} is malformed")
            continue
        expected_hash, raw_path = match.groups()
        relative_path = Path(raw_path)
        if (
            relative_path.is_absolute()
            or ".." in relative_path.parts
            or "\\" in raw_path
        ):
            result.fail(f"{label} record {line_number} is not root-confined")
            continue
        normalized = relative_path.as_posix()
        if normalized in records:
            result.fail(f"{label} contains duplicate path {normalized}")
            continue
        path = (manifest_root / relative_path).resolve()
        try:
            path.relative_to(manifest_root.resolve())
        except ValueError:
            result.fail(f"{label} record {line_number} escapes its root")
            continue
        if path == manifest_path.resolve():
            result.fail(f"{label} must exclude itself")
            continue
        if not path.is_file():
            result.fail(f"{label} file is missing: {normalized}")
            continue
        if file_sha256(path) != expected_hash:
            result.fail(f"{label} SHA-256 mismatch: {normalized}")
        records[normalized] = expected_hash
    return records


def _verify_packet_manifest(
    result: VerificationResult,
    packet_root: Path,
    manifest_path: Path,
) -> None:
    """Require the manifest to cover every packet file except itself."""
    records = _parse_sha_manifest(
        result,
        packet_root,
        manifest_path,
        "artifact manifest",
        expected_records=None,
    )
    expected_paths = {
        path.relative_to(packet_root).as_posix()
        for path in packet_root.rglob("*")
        if path.is_file() and path != manifest_path
    }
    if set(records) != expected_paths:
        result.fail("artifact manifest path set does not match packet files")
    result.checks["manifest_records"] = len(records)


def _parse_inventory(
    result: VerificationResult,
    packet_root: Path,
    inventory_path: Path,
) -> None:
    """Validate the non-self-referential artifact inventory."""
    try:
        lines = inventory_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        result.fail(f"artifact inventory is not readable: {error}")
        return
    if not lines or lines[0] != "path\tbytes\tsha256":
        result.fail("artifact inventory header is invalid")
        return

    records: dict[str, tuple[int, str]] = {}
    for line_number, line in enumerate(lines[1:], start=2):
        fields = line.split("\t")
        if len(fields) != 3:
            result.fail(f"artifact inventory record {line_number} is malformed")
            continue
        raw_path, raw_bytes, expected_hash = fields
        relative_path = Path(raw_path)
        if (
            relative_path.is_absolute()
            or ".." in relative_path.parts
            or "\\" in raw_path
        ):
            result.fail(f"artifact inventory record {line_number} is not root-confined")
            continue
        try:
            expected_bytes = int(raw_bytes)
        except ValueError:
            result.fail(f"artifact inventory record {line_number} has invalid bytes")
            continue
        if expected_bytes < 0 or SHA256_PATTERN.fullmatch(expected_hash) is None:
            result.fail(f"artifact inventory record {line_number} has invalid shape")
            continue
        normalized = relative_path.as_posix()
        if normalized in records:
            result.fail(f"artifact inventory duplicates {normalized}")
            continue
        path = packet_root / relative_path
        if not path.is_file():
            result.fail(f"artifact inventory file is missing: {normalized}")
            continue
        if path.stat().st_size != expected_bytes:
            result.fail(f"artifact inventory byte count mismatch: {normalized}")
        if file_sha256(path) != expected_hash:
            result.fail(f"artifact inventory SHA-256 mismatch: {normalized}")
        records[normalized] = (expected_bytes, expected_hash)

    expected_paths = {
        path.relative_to(packet_root).as_posix()
        for path in packet_root.rglob("*")
        if path.is_file()
        and path.name not in {"artifact-inventory.txt", "artifact-manifest.sha256"}
    }
    if set(records) != expected_paths:
        result.fail("artifact inventory path set does not match core artifacts")
    result.checks["inventory_records"] = len(records)


def _forbidden_key_path(
    value: object,
    prefix: str = "$",
) -> str | None:
    """Find the first key that could carry decoded transcript wording."""
    if isinstance(value, dict):
        for key, child in value.items():
            key_string = str(key)
            if key_string.lower() in FORBIDDEN_WORDING_KEYS:
                return f"{prefix}.{key_string}"
            found = _forbidden_key_path(child, f"{prefix}.{key_string}")
            if found is not None:
                return found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found = _forbidden_key_path(child, f"{prefix}[{index}]")
            if found is not None:
                return found
    return None


def _verify_no_wording_keys(
    result: VerificationResult,
    document: object,
    label: str,
) -> None:
    """Reject any field capable of retaining decoded wording."""
    forbidden_path = _forbidden_key_path(document)
    if forbidden_path is not None:
        result.fail(f"{label} contains forbidden decoded-wording key {forbidden_path}")


def _verify_no_secret_patterns(
    result: VerificationResult,
    packet_root: Path,
) -> None:
    """Reject common credential markers from every generated packet file."""
    for path in sorted(packet_root.rglob("*")):
        if not path.is_file():
            continue
        try:
            value = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            result.fail("packet contains a non-text artifact")
            continue
        if any(pattern.search(value) for pattern in SECRET_PATTERNS):
            result.fail("packet contains a credential-like value")


def _verify_slug(result: VerificationResult, value: object, label: str) -> None:
    """Require a bounded, wording-free identifier."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 240
        or SLUG_PATTERN.fullmatch(value) is None
    ):
        result.fail(f"{label} must be a bounded identifier")


def _validate_gate_matrix(
    result: VerificationResult,
    document: dict[str, Any],
) -> list[dict[str, Any]]:
    """Validate all seven legacy gates exactly once."""
    if document.get("schema_version") != GATE_MATRIX_SCHEMA_VERSION:
        result.fail("gate matrix schema_version is invalid")
    gates = document.get("gates")
    if not isinstance(gates, list):
        result.fail("gate matrix gates must be an array")
        return []
    if any(not isinstance(gate, dict) for gate in gates):
        result.fail("every legacy gate must be an object")
        return []

    gate_ids = [gate.get("gate_id") for gate in gates]
    if len(gate_ids) != len(set(gate_ids)):
        result.fail("legacy gate IDs must be unique")
    if set(gate_ids) != set(EXPECTED_LEGACY_GATE_IDS) or len(gates) != 7:
        result.fail("legacy gate IDs must match the exact seven-gate contract")

    allowed_classifications = {
        CONFIRMED_CLASSIFICATION,
        "EXPECTED_HISTORICAL_GAP",
        "UNVERIFIED_COMPARISON_ALIGNMENT",
        "CLEARED_NO_SUBSTANTIVE_FAILURE",
    }
    for gate in gates:
        if set(gate) != GATE_KEYS:
            result.fail("legacy gate fields do not match the sanitized schema")
            continue
        _verify_slug(result, gate.get("gate_id"), "legacy gate ID")
        _verify_slug(
            result,
            gate.get("legacy_failure_class"),
            "legacy failure class",
        )
        _verify_slug(
            result,
            gate.get("proof_class"),
            "legacy proof class",
        )
        _verify_slug(
            result,
            gate.get("disposition_group"),
            "legacy disposition group",
        )
        if gate.get("classification") not in allowed_classifications:
            result.fail("legacy gate classification is invalid")
        if gate.get("candidate_causality") not in {
            "CONFIRMED",
            "NOT_CANDIDATE_ON",
            "NOT_CONFIRMED",
            "UNVERIFIED",
        }:
            result.fail("legacy gate candidate_causality is invalid")
        if not isinstance(gate.get("disposition_trigger"), bool):
            result.fail("legacy gate disposition_trigger must be boolean")
        if gate.get("blocking_effect") not in {
            "REJECT",
            "NONE",
            "SUPPLEMENTAL_COMPARISON_REQUIRED",
        }:
            result.fail("legacy gate blocking_effect is invalid")
        proof = gate.get("proof")
        if not isinstance(proof, dict):
            result.fail("legacy gate proof must be an object")
        elif not set(proof).issubset(ALLOWED_GATE_PROOF_KEYS):
            result.fail("legacy gate proof fields exceed the sanitized schema")
    result.checks["legacy_gate_count"] = len(gates)
    return gates


def _validate_source_matrix(
    result: VerificationResult,
    document: dict[str, Any],
) -> list[dict[str, Any]]:
    """Validate all 29 source-chip alerts exactly once."""
    if document.get("schema_version") != SOURCE_MATRIX_SCHEMA_VERSION:
        result.fail("source-chip matrix schema_version is invalid")
    alerts = document.get("alerts")
    if not isinstance(alerts, list):
        result.fail("source-chip alerts must be an array")
        return []
    if any(not isinstance(alert, dict) for alert in alerts):
        result.fail("every source-chip alert must be an object")
        return []
    if len(alerts) != 29:
        result.fail("source-chip alert count must be exactly 29")

    alert_ids = [alert.get("alert_id") for alert in alerts]
    if len(alert_ids) != len(set(alert_ids)):
        result.fail("source alert IDs must be unique")
    identities = [
        (
            alert.get("fixture"),
            alert.get("segment_id"),
            alert.get("row_number"),
        )
        for alert in alerts
    ]
    if len(identities) != len(set(identities)):
        result.fail("source alert identities must be unique")

    input_counts: Counter[str] = Counter()
    allowed_adjudications = {
        "TRUTH_ALIGNED_HEURISTIC_FALSE_POSITIVE",
        "BASELINE_PERSISTENT_CONFIRMED_ROLE_ERROR",
        CONFIRMED_CLASSIFICATION,
        "EXPLAINED_REFERENCE_GAP",
        "UNSCORABLE_REFERENCE_GAP",
    }
    for alert in alerts:
        if set(alert) != SOURCE_ALERT_KEYS:
            result.fail("source-chip alert fields do not match the sanitized schema")
            continue
        _verify_slug(result, alert.get("alert_id"), "source alert ID")
        _verify_slug(result, alert.get("fixture"), "source fixture")
        _verify_slug(result, alert.get("segment_id"), "source segment ID")
        _verify_slug(
            result,
            alert.get("disposition_group"),
            "source disposition group",
        )
        if not isinstance(alert.get("row_number"), int):
            result.fail("source row_number must be an integer")
        input_classification = alert.get("input_classification")
        if input_classification not in EXPECTED_SOURCE_INPUT_COUNTS:
            result.fail("source input classification is invalid")
        else:
            input_counts[input_classification] += 1
        if alert.get("adjudication") not in allowed_adjudications:
            result.fail("source adjudication is invalid")
        if alert.get("candidate_causality") not in {
            "CONFIRMED",
            "NOT_A_ROLE_ERROR",
            "NOT_CANDIDATE_CAUSED",
            "UNVERIFIED",
        }:
            result.fail("source candidate_causality is invalid")
        codes = alert.get("codes")
        if (
            not isinstance(codes, list)
            or not codes
            or any(
                not isinstance(code, str) or re.fullmatch(r"[a-z0-9_]+", code) is None
                for code in codes
            )
        ):
            result.fail("source codes must be non-empty identifier strings")
        related = alert.get("related_legacy_gate_ids")
        if not isinstance(related, list) or any(
            item not in EXPECTED_LEGACY_GATE_IDS for item in related
        ):
            result.fail("source related legacy gate IDs are invalid")
        proof = alert.get("proof")
        if not isinstance(proof, dict):
            result.fail("source alert proof must be an object")
        elif not set(proof).issubset(ALLOWED_SOURCE_PROOF_KEYS):
            result.fail("source alert proof fields exceed the sanitized schema")
    if dict(input_counts) != EXPECTED_SOURCE_INPUT_COUNTS:
        result.fail("source input classifications must retain the 22/3/4 split")
    result.checks["source_chip_alert_count"] = len(alerts)
    result.checks["source_input_classifications"] = dict(input_counts)
    return alerts


def derive_disposition(
    gates: Iterable[dict[str, Any]],
    alerts: Iterable[dict[str, Any]],
) -> str:
    """Apply the frozen rejection-first M05D decision rule."""
    entries = [*gates, *alerts]
    confirmed = any(
        (
            entry.get("classification") == CONFIRMED_CLASSIFICATION
            or entry.get("adjudication") == CONFIRMED_CLASSIFICATION
        )
        and entry.get("candidate_causality") == "CONFIRMED"
        for entry in entries
    )
    if confirmed:
        return "REJECT_CANDIDATE_KEEP_FLAG_OFF"

    unverified = any(
        entry.get("candidate_causality") == "UNVERIFIED"
        or entry.get("classification") in UNVERIFIED_CLASSIFICATIONS
        or entry.get("adjudication") in UNVERIFIED_CLASSIFICATIONS
        for entry in entries
    )
    if unverified:
        return "BLOCKED_UNVERIFIED"
    return "ELIGIBLE_FOR_SEPARATE_SUPPLEMENTAL_COMPARISON_APPROVAL"


def _validate_receipts(
    result: VerificationResult,
    document: dict[str, Any],
) -> None:
    """Require all four pre-build static and behavioral commands to pass."""
    if document.get("schema_version") != RECEIPTS_SCHEMA_VERSION:
        result.fail("verification receipts schema_version is invalid")
    commands = document.get("commands")
    if not isinstance(commands, list):
        result.fail("verification receipts commands must be an array")
        return
    names: list[object] = []
    for command in commands:
        if not isinstance(command, dict):
            result.fail("verification receipt command must be an object")
            continue
        name = command.get("name")
        names.append(name)
        if command.get("exit_code") != 0:
            result.fail(f"verification receipt {name!r} did not pass")
    if len(names) != len(set(names)) or set(names) != EXPECTED_RECEIPT_NAMES:
        result.fail("verification receipts must contain four unique commands")
    result.checks["verification_receipts"] = len(commands)


def _validate_source_identities(
    result: VerificationResult,
    workspace_root: Path,
    document: dict[str, Any],
) -> None:
    """Rehash every external input bound into the adjudication packet."""
    if document.get("schema_version") != SOURCE_IDENTITIES_SCHEMA_VERSION:
        result.fail("source identities schema_version is invalid")
    records = document.get("records")
    if not isinstance(records, list) or not records:
        result.fail("source identity records must be a non-empty array")
        return
    labels: list[object] = []
    paths: list[object] = []
    for record in records:
        if not isinstance(record, dict):
            result.fail("source identity record must be an object")
            continue
        labels.append(record.get("label"))
        paths.append(record.get("path"))
        _verify_slug(result, record.get("label"), "source identity label")
        _verify_file_record(
            result,
            workspace_root,
            record,
            "source identity",
        )
    if len(labels) != len(set(labels)):
        result.fail("source identity labels must be unique")
    if len(paths) != len(set(paths)):
        result.fail("source identity paths must be unique")
    result.checks["source_identity_records"] = len(records)


def _validate_verifier_result(
    result: VerificationResult,
    document: dict[str, Any],
    expected_disposition: str,
) -> None:
    """Bind the preseal semantic result to the sealed decision."""
    expected = {
        "disposition": expected_disposition,
        "full_campaign_pass": False,
        "legacy_gate_count": 7,
        "ok": True,
        "phase": "preseal",
        "promotion_authorized": False,
        "schema_version": RESULT_SCHEMA_VERSION,
        "source_chip_alert_count": 29,
    }
    if document != expected:
        result.fail("verifier-result.json does not match the packet decision")


def verify_decision_packet(
    workspace_root: Path,
    decision_path: Path,
    *,
    require_read_only: bool = True,
) -> VerificationResult:
    """Verify one M05D packet without mutating it."""
    result = VerificationResult()
    resolved_root = workspace_root.resolve()
    resolved_decision = decision_path.resolve()
    try:
        resolved_decision.relative_to(resolved_root)
    except ValueError:
        result.fail("decision path must stay inside the workspace")
        return result
    if not resolved_decision.is_file():
        result.fail("decision.json is missing")
        return result

    packet_root = resolved_decision.parent
    if require_read_only:
        _verify_read_only_tree(result, packet_root)

    manifest_path = packet_root / "artifact-manifest.sha256"
    inventory_path = packet_root / "artifact-inventory.txt"
    if not manifest_path.is_file():
        result.fail("artifact manifest is missing")
    else:
        _verify_packet_manifest(result, packet_root, manifest_path)
    if not inventory_path.is_file():
        result.fail("artifact inventory is missing")
    else:
        _parse_inventory(result, packet_root, inventory_path)
    _verify_no_secret_patterns(result, packet_root)

    decision = _load_json(result, resolved_decision, "decision")
    if decision is None:
        return result
    _verify_no_wording_keys(result, decision, "decision")
    if decision.get("schema_version") != DECISION_SCHEMA_VERSION:
        result.fail("decision schema_version is invalid")
    if decision.get("claim_scope") != "sealed CPU-only quality adjudication":
        result.fail("decision claim_scope is invalid")
    if decision.get("disposition") not in ALLOWED_DISPOSITIONS:
        result.fail("decision disposition is not one of the three allowed values")
    if decision.get("promotion_authorized") is not False:
        result.fail("promotion_authorized must be false")
    if decision.get("full_campaign_pass") is not False:
        result.fail("full_campaign_pass must be false")
    if decision.get("runtime_calls_added") != 0:
        result.fail("runtime_calls_added must be zero")
    if decision.get("legacy_gate_count") != 7:
        result.fail("decision legacy_gate_count must be 7")
    if decision.get("source_chip_alert_count") != 29:
        result.fail("decision source_chip_alert_count must be 29")

    expected_manifest_path = manifest_path.relative_to(resolved_root).as_posix()
    if decision.get("packet_manifest_path") != expected_manifest_path:
        result.fail("decision packet_manifest_path is invalid")

    bound_paths: dict[str, Path] = {}
    expected_files = {
        "decision_markdown": packet_root / "decision.md",
        "gate_matrix": packet_root / "gate-matrix.json",
        "source_chip_matrix": packet_root / "source-chip-matrix.json",
        "source_identities": packet_root / "source-identities.json",
        "verification_receipts": packet_root / "verification-receipts.json",
    }
    for field_name, expected_path in expected_files.items():
        path = _verify_file_record(
            result,
            resolved_root,
            decision.get(field_name),
            field_name.replace("_", " "),
            expected_path=expected_path.relative_to(resolved_root).as_posix(),
        )
        if path is not None:
            bound_paths[field_name] = path

    gate_document = (
        _load_json(result, bound_paths["gate_matrix"], "gate matrix")
        if "gate_matrix" in bound_paths
        else None
    )
    source_document = (
        _load_json(
            result,
            bound_paths["source_chip_matrix"],
            "source-chip matrix",
        )
        if "source_chip_matrix" in bound_paths
        else None
    )
    identities_document = (
        _load_json(
            result,
            bound_paths["source_identities"],
            "source identities",
        )
        if "source_identities" in bound_paths
        else None
    )
    receipts_document = (
        _load_json(
            result,
            bound_paths["verification_receipts"],
            "verification receipts",
        )
        if "verification_receipts" in bound_paths
        else None
    )

    gates: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []
    if gate_document is not None:
        _verify_no_wording_keys(result, gate_document, "gate matrix")
        gates = _validate_gate_matrix(result, gate_document)
    if source_document is not None:
        _verify_no_wording_keys(result, source_document, "source-chip matrix")
        alerts = _validate_source_matrix(result, source_document)
    if identities_document is not None:
        _verify_no_wording_keys(
            result,
            identities_document,
            "source identities",
        )
        _validate_source_identities(
            result,
            resolved_root,
            identities_document,
        )
    if receipts_document is not None:
        _verify_no_wording_keys(
            result,
            receipts_document,
            "verification receipts",
        )
        _validate_receipts(result, receipts_document)

    computed_disposition = derive_disposition(gates, alerts)
    result.checks["computed_disposition"] = computed_disposition
    if decision.get("disposition") != computed_disposition:
        result.fail("decision disposition does not match the frozen rule")

    markdown_path = bound_paths.get("decision_markdown")
    if markdown_path is not None:
        try:
            markdown = markdown_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            result.fail(f"decision markdown is not readable: {error}")
        else:
            if f"Disposition: {decision.get('disposition')}" not in markdown:
                result.fail("decision markdown disposition is inconsistent")
            if "Promotion authorized: false." not in markdown:
                result.fail("decision markdown must deny promotion")
            if "Full campaign pass: false." not in markdown:
                result.fail("decision markdown must deny full-campaign pass")
            if "Runtime calls added: 0." not in markdown:
                result.fail("decision markdown must record zero runtime calls")

    verifier_result_path = packet_root / "verifier-result.json"
    verifier_result_document = _load_json(
        result,
        verifier_result_path,
        "verifier result",
    )
    if verifier_result_document is not None:
        _verify_no_wording_keys(
            result,
            verifier_result_document,
            "verifier result",
        )
        _validate_verifier_result(
            result,
            verifier_result_document,
            computed_disposition,
        )

    result.disposition = computed_disposition if result.ok else "BLOCKED_UNVERIFIED"
    return result


def _evidence_record(
    workspace_root: Path,
    path: Path,
    *,
    label: str | None = None,
) -> dict[str, object]:
    """Create one generated workspace-relative source binding."""
    resolved_root = workspace_root.resolve()
    resolved_path = path.resolve()
    try:
        relative_path = resolved_path.relative_to(resolved_root)
    except ValueError as error:
        raise EvidenceError("evidence path escapes the workspace") from error
    record: dict[str, object] = {
        "path": relative_path.as_posix(),
        "bytes": resolved_path.stat().st_size,
        "sha256": file_sha256(resolved_path),
    }
    if label is not None:
        record = {"label": label, **record}
    return record


def _verify_frozen_inputs(workspace_root: Path) -> None:
    """Stop before packet creation when any selected frozen input drifts."""
    checks = VerificationResult()
    selected_manifests = (
        (
            workspace_root / CORPUS_ON_ROOT,
            workspace_root / CORPUS_ON_ROOT / "artifact-manifest.sha256",
            "M05C artifact manifest",
            167,
        ),
        (
            workspace_root / M05B_PACKET_ROOT,
            workspace_root / M05B_PACKET_ROOT / "packet-manifest.sha256",
            "M05B packet manifest",
            5,
        ),
        (
            workspace_root / M05B_VERIFICATION_ROOT,
            workspace_root / M05B_VERIFICATION_ROOT / "artifact-manifest.sha256",
            "M05B verification manifest",
            23,
        ),
    )
    for root, manifest, label, expected_records in selected_manifests:
        _parse_sha_manifest(
            checks,
            root,
            manifest,
            label,
            expected_records=expected_records,
        )

    for raw_path, expected_hash in FROZEN_SOURCE_HASHES.items():
        path = workspace_root / raw_path
        if not path.is_file():
            checks.fail(f"frozen source is missing: {raw_path}")
        elif file_sha256(path) != expected_hash:
            checks.fail(f"frozen source SHA-256 mismatch: {raw_path}")

    legacy = _load_build_json(
        workspace_root / LEGACY_ADJUDICATION_PATH,
        "legacy adjudication",
    )
    failure_classes = legacy.get("failure_classes")
    if not isinstance(failure_classes, list):
        checks.fail("legacy failure_classes must be an array")
    else:
        counts = [
            item.get("count") for item in failure_classes if isinstance(item, dict)
        ]
        if (
            len(counts) != len(failure_classes)
            or any(not isinstance(count, int) for count in counts)
            or sum(counts) != 7
        ):
            checks.fail("legacy adjudication must decompose exactly seven gates")
    if legacy.get("classification_totals") != {
        "substantive_quality_findings": 5,
        "expected_historical_gaps": 2,
    }:
        checks.fail("legacy adjudication must retain the 5/2 split")
    if legacy.get("promotion_authorized") is not False:
        checks.fail("legacy adjudication must not authorize promotion")

    source = _load_build_json(
        workspace_root / SOURCE_ADJUDICATION_PATH,
        "source-chip adjudication",
    )
    findings = source.get("findings")
    if not isinstance(findings, list) or len(findings) != 29:
        checks.fail("source-chip adjudication must contain 29 findings")
    else:
        identities = [
            (
                finding.get("fixture"),
                finding.get("segment_id"),
                finding.get("row_number"),
            )
            for finding in findings
            if isinstance(finding, dict)
        ]
        if len(identities) != 29 or len(set(identities)) != 29:
            checks.fail("source-chip findings must have 29 unique identities")
    if source.get("classifications") != EXPECTED_SOURCE_INPUT_COUNTS:
        checks.fail("source-chip adjudication must retain the 22/3/4 split")
    if source.get("promotion_authorized") is not False:
        checks.fail("source-chip adjudication must not authorize promotion")

    if checks.failures:
        raise EvidenceError("; ".join(checks.failures))


def _attribution_state(role: object, expected_role: object) -> str:
    """Match the unchanged legacy verifier's three-state ordering."""
    expected = expected_role.upper() if isinstance(expected_role, str) else None
    visible = role.upper() if isinstance(role, str) else ""
    if expected is None or visible not in CONFIDENT_ROLES:
        return "unresolved"
    return "correct" if visible == expected else "wrong"


def _fixture_path(arm_root: Path, fixture: str, name: str) -> Path:
    """Resolve one corpus artifact under the selected arm."""
    return arm_root / "corpus" / fixture / name


def _load_fixture_lane(
    workspace_root: Path,
    arm_root: Path,
    fixture: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[Path]]:
    """Load one corrected transcript and its aligned row diagnostics."""
    transcript_path = workspace_root / _fixture_path(
        arm_root,
        fixture,
        "corrected-transcript.json",
    )
    diagnostics_path = workspace_root / _fixture_path(
        arm_root,
        fixture,
        "corrected-row-diagnostics.json",
    )
    transcript = _load_build_json(transcript_path, "corrected transcript")
    diagnostics = _load_build_json(
        diagnostics_path,
        "corrected row diagnostics",
    )
    segments = transcript.get("segments")
    rows = diagnostics.get("rows")
    _require(isinstance(segments, list), "transcript segments must be an array")
    _require(isinstance(rows, list), "diagnostic rows must be an array")
    _require(
        len(segments) == len(rows),
        "transcript and diagnostic row counts must match",
    )
    _require(
        all(isinstance(item, dict) for item in segments),
        "every transcript segment must be an object",
    )
    _require(
        all(isinstance(item, dict) for item in rows),
        "every diagnostic row must be an object",
    )
    return segments, rows, [transcript_path, diagnostics_path]


def _aligned_row_proof(
    off_segment: dict[str, Any],
    on_segment: dict[str, Any],
    expected_role: object,
    row_index: int,
) -> dict[str, Any]:
    """Return sanitized row evidence with wording reduced to fingerprints."""
    timing_aligned = (
        abs(float(off_segment.get("start", 0.0)) - float(on_segment.get("start", 0.0)))
        <= ROW_TIME_TOLERANCE
        and abs(float(off_segment.get("end", 0.0)) - float(on_segment.get("end", 0.0)))
        <= ROW_TIME_TOLERANCE
    )
    return {
        "end": float(on_segment.get("end", 0.0)),
        "expected_role": expected_role,
        "off_role": str(off_segment.get("role", "")).upper(),
        "off_segment_id": str(off_segment.get("segment_id", "")),
        "off_state": _attribution_state(
            off_segment.get("role"),
            expected_role,
        ),
        "off_text_sha256": text_sha256(off_segment.get("text", "")),
        "on_role": str(on_segment.get("role", "")).upper(),
        "on_segment_id": str(on_segment.get("segment_id", "")),
        "on_state": _attribution_state(
            on_segment.get("role"),
            expected_role,
        ),
        "on_text_sha256": text_sha256(on_segment.get("text", "")),
        "row_index": row_index,
        "row_number": row_index + 1,
        "start": float(on_segment.get("start", 0.0)),
        "text_equal": (
            str(off_segment.get("text", "")) == str(on_segment.get("text", ""))
        ),
        "timing_aligned": timing_aligned,
    }


def _build_c03_gates(
    workspace_root: Path,
) -> tuple[list[dict[str, Any]], list[Path]]:
    """Recompute the single c03 row regression behind two legacy gates."""
    off_segments, off_rows, off_paths = _load_fixture_lane(
        workspace_root,
        CORPUS_OFF_ROOT,
        C03_FIXTURE,
    )
    on_segments, on_rows, on_paths = _load_fixture_lane(
        workspace_root,
        CORPUS_ON_ROOT,
        C03_FIXTURE,
    )
    _require(
        len(off_segments) == len(on_segments),
        "c03 row counts differ between arms",
    )

    worsened: list[dict[str, Any]] = []
    newly_wrong: list[dict[str, Any]] = []
    for index, (off_segment, on_segment, off_row, on_row) in enumerate(
        zip(off_segments, on_segments, off_rows, on_rows, strict=True)
    ):
        expected_role = off_row.get("expected_role")
        _require(
            on_row.get("expected_role") == expected_role,
            "c03 expected roles differ between arms",
        )
        proof = _aligned_row_proof(
            off_segment,
            on_segment,
            expected_role,
            index,
        )
        _require(proof["timing_aligned"] is True, "c03 row timing drifted")
        _require(proof["text_equal"] is True, "c03 row wording drifted")
        if STATE_RANK[proof["on_state"]] < STATE_RANK[proof["off_state"]]:
            worsened.append(proof)
        if proof["on_state"] == "wrong" and proof["off_state"] != "wrong":
            newly_wrong.append(proof)

    _require(len(worsened) == 1, "c03 must have exactly one worsened row")
    _require(
        len(newly_wrong) == 1,
        "c03 must have exactly one newly confident-wrong row",
    )
    _require(
        worsened[0]["row_index"] == newly_wrong[0]["row_index"],
        "c03 legacy gates must resolve to the same row",
    )
    proof = worsened[0]
    _require(
        proof["off_state"] == "correct" and proof["on_state"] == "wrong",
        "c03 regression must be correct-to-wrong",
    )
    disposition_group = f"c03-{proof['on_segment_id']}-regression"
    common = {
        "blocking_effect": "REJECT",
        "candidate_causality": "CONFIRMED",
        "classification": CONFIRMED_CLASSIFICATION,
        "disposition_group": disposition_group,
        "legacy_failure_class": "candidate_on_c03_causal_regression",
        "proof": proof,
        "proof_class": "MECHANICAL_ALIGNED_ROW_COMPARISON",
    }
    return (
        [
            {
                **common,
                "disposition_trigger": True,
                "gate_id": "c03_worsened_corrected_row",
            },
            {
                **common,
                "disposition_trigger": False,
                "gate_id": "c03_newly_confident_wrong_corrected_row",
            },
        ],
        [*off_paths, *on_paths],
    )


def _diagnostic_summary(
    workspace_root: Path,
    arm_root: Path,
    fixture: str,
) -> tuple[dict[str, Any], Path]:
    """Load one corrected diagnostic summary."""
    path = workspace_root / _fixture_path(
        arm_root,
        fixture,
        "corrected-row-diagnostics.json",
    )
    document = _load_build_json(path, "corrected row diagnostics")
    summary = document.get("summary")
    _require(isinstance(summary, dict), "diagnostic summary must be an object")
    return summary, path


def _metric_gate(
    gate_id: str,
    *,
    comparison: str,
    off_numerator: int,
    on_numerator: int,
    clean_rows: int,
    disposition_trigger: bool,
) -> dict[str, Any]:
    """Create one independently recomputed c08 metric gate."""
    off_rate = off_numerator / clean_rows
    on_rate = on_numerator / clean_rows
    regressed = on_rate < off_rate if comparison == "<" else on_rate > off_rate
    _require(regressed, f"{gate_id} is not reproduced")
    return {
        "blocking_effect": "REJECT",
        "candidate_causality": "CONFIRMED",
        "classification": CONFIRMED_CLASSIFICATION,
        "disposition_group": "c08-corrected-attribution-regression",
        "disposition_trigger": disposition_trigger,
        "gate_id": gate_id,
        "legacy_failure_class": "candidate_on_c08_metric_and_text_regression",
        "proof": {
            "clean_reference_rows": clean_rows,
            "comparison": comparison,
            "off_numerator": off_numerator,
            "off_rate": off_rate,
            "on_numerator": on_numerator,
            "on_rate": on_rate,
        },
        "proof_class": "MECHANICAL_DIAGNOSTIC_RATE_RECOMPUTATION",
    }


def _build_c08_gates(
    workspace_root: Path,
) -> tuple[list[dict[str, Any]], list[Path]]:
    """Recompute c08 rates and fingerprint the first text-alignment failure."""
    off_segments, off_rows, off_paths = _load_fixture_lane(
        workspace_root,
        CORPUS_OFF_ROOT,
        C08_FIXTURE,
    )
    on_segments, on_rows, on_paths = _load_fixture_lane(
        workspace_root,
        CORPUS_ON_ROOT,
        C08_FIXTURE,
    )
    off_summary, off_diag_path = _diagnostic_summary(
        workspace_root,
        CORPUS_OFF_ROOT,
        C08_FIXTURE,
    )
    on_summary, on_diag_path = _diagnostic_summary(
        workspace_root,
        CORPUS_ON_ROOT,
        C08_FIXTURE,
    )
    _require(
        len(off_segments) == len(on_segments),
        "c08 row counts differ between arms",
    )

    clean_off = int(off_summary.get("clean_reference_rows", -1))
    clean_on = int(on_summary.get("clean_reference_rows", -1))
    _require(clean_off == clean_on == 216, "c08 clean row count drifted")
    strict_off = int(off_summary.get("strict_correct_rows", -1))
    strict_on = int(on_summary.get("strict_correct_rows", -1))
    incorrect_off = int(off_summary.get("incorrect_confident_rows", -1))
    incorrect_on = int(on_summary.get("incorrect_confident_rows", -1))
    _require(
        (strict_off, strict_on, incorrect_off, incorrect_on) == (196, 187, 20, 29),
        "c08 metric numerators drifted",
    )

    mismatches: list[dict[str, Any]] = []
    for index, (off_segment, on_segment, off_row, on_row) in enumerate(
        zip(off_segments, on_segments, off_rows, on_rows, strict=True)
    ):
        expected_role = off_row.get("expected_role")
        _require(
            on_row.get("expected_role") == expected_role,
            "c08 expected roles differ between arms",
        )
        proof = _aligned_row_proof(
            off_segment,
            on_segment,
            expected_role,
            index,
        )
        _require(proof["timing_aligned"] is True, "c08 row timing drifted")
        if proof["text_equal"] is False:
            mismatches.append(proof)

    _require(mismatches, "c08 text mismatch is not reproduced")
    first_mismatch = dict(mismatches[0])
    _require(
        first_mismatch["row_index"] == 174,
        "c08 first text mismatch row drifted",
    )
    first_mismatch["first_mismatch_row_index"] = first_mismatch.pop("row_index")
    first_mismatch["total_text_mismatch_rows"] = len(mismatches)
    return (
        [
            _metric_gate(
                "c08_corrected_strict_regression",
                comparison="<",
                off_numerator=strict_off,
                on_numerator=strict_on,
                clean_rows=clean_off,
                disposition_trigger=True,
            ),
            _metric_gate(
                "c08_incorrect_confident_regression",
                comparison=">",
                off_numerator=incorrect_off,
                on_numerator=incorrect_on,
                clean_rows=clean_off,
                disposition_trigger=False,
            ),
            {
                "blocking_effect": "NONE",
                "candidate_causality": "UNVERIFIED",
                "classification": "UNVERIFIED_COMPARISON_ALIGNMENT",
                "disposition_group": "c08-corrected-text-alignment",
                "disposition_trigger": False,
                "gate_id": "c08_corrected_text_alignment",
                "legacy_failure_class": ("candidate_on_c08_metric_and_text_regression"),
                "proof": first_mismatch,
                "proof_class": "MECHANICAL_FINGERPRINT_COMPARISON",
            },
        ],
        list(
            dict.fromkeys(
                [
                    *off_paths,
                    *on_paths,
                    off_diag_path,
                    on_diag_path,
                ]
            )
        ),
    )


def _parse_json_log_events(path: Path) -> list[dict[str, Any]]:
    """Parse captured JSON event envelopes without returning raw log lines."""
    events: list[dict[str, Any]] = []
    for line in path.read_text(
        encoding="utf-8",
        errors="replace",
    ).splitlines():
        brace_index = line.find("{")
        if brace_index < 0:
            continue
        try:
            event = json.loads(line[brace_index:])
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _build_historical_gates(
    workspace_root: Path,
) -> tuple[list[dict[str, Any]], list[Path]]:
    """Mechanically retain the two known historical d2c09 gaps."""
    fixture_root = workspace_root / CORPUS_OFF_ROOT / "corpus" / D2C09_FIXTURE
    diagnostics_path = fixture_root / "corrected-row-diagnostics.json"
    transcript_path = fixture_root / "corrected-transcript.json"
    _require(
        not diagnostics_path.exists() and not transcript_path.exists(),
        "historical d2c09 corrected artifacts are no longer missing",
    )

    quality_path = fixture_root / "quality.json"
    log_path = workspace_root / CORPUS_OFF_ROOT / "correction-logs.txt"
    quality = _load_build_json(quality_path, "historical d2c09 quality")
    session_id = quality.get("session_id")
    _require(isinstance(session_id, str) and session_id, "session ID is missing")
    completion_events = [
        event
        for event in _parse_json_log_events(log_path)
        if str(event.get("event", "")).startswith("correction.completed")
        and event.get("session_id") == session_id
    ]
    _require(
        not completion_events,
        "historical d2c09 completion event is no longer absent",
    )

    attestation_path = workspace_root / M05B_ATTESTATION_PATH
    attestation = _load_build_json(attestation_path, "M05B attestation")
    _require(
        attestation.get("verdict") == "PASS_HYBRID_FLAG_OFF",
        "M05B recovery verdict drifted",
    )
    _require(
        attestation.get("full_campaign_status") == "NOT_EVALUATED",
        "M05B full-campaign scope drifted",
    )
    _require(
        attestation.get("promotion_authorized") is False,
        "M05B unexpectedly authorizes promotion",
    )

    common = {
        "blocking_effect": "SUPPLEMENTAL_COMPARISON_REQUIRED",
        "candidate_causality": "NOT_CANDIDATE_ON",
        "classification": "EXPECTED_HISTORICAL_GAP",
        "disposition_trigger": False,
        "proof_class": "MECHANICAL_HISTORICAL_SCOPE_CHECK",
    }
    return (
        [
            {
                **common,
                "disposition_group": "historical-d2c09-corrected-artifacts",
                "gate_id": "historical_d2c09_missing_corrected_artifacts",
                "legacy_failure_class": (
                    "historical_corpus_off_d2c09_missing_corrected_artifacts"
                ),
                "proof": {
                    "historical_corrected_diagnostics_present": False,
                    "historical_corrected_transcript_present": False,
                    "legacy_reported": True,
                    "m05b_full_campaign_status": "NOT_EVALUATED",
                    "m05b_recovery_verdict": "PASS_HYBRID_FLAG_OFF",
                },
            },
            {
                **common,
                "disposition_group": "historical-d2c09-completion-event",
                "gate_id": "historical_d2c09_missing_completion_event",
                "legacy_failure_class": ("historical_d2c09_completion_event_absent"),
                "proof": {
                    "completion_event_count": 0,
                    "legacy_reported": True,
                    "m05b_full_campaign_status": "NOT_EVALUATED",
                    "m05b_recovery_verdict": "PASS_HYBRID_FLAG_OFF",
                },
            },
        ],
        [quality_path, log_path, attestation_path],
    )


def _fixture_lane_index(
    workspace_root: Path,
    fixture: str,
    cache: dict[
        tuple[str, str],
        tuple[dict[str, tuple[int, dict[str, Any], dict[str, Any]]], list[Path]],
    ],
) -> tuple[
    dict[str, tuple[int, dict[str, Any], dict[str, Any]]],
    dict[str, tuple[int, dict[str, Any], dict[str, Any]]],
    list[Path],
]:
    """Index both arms by stable segment ID for source-alert cross-mapping."""
    indexed: dict[
        str,
        tuple[
            dict[str, tuple[int, dict[str, Any], dict[str, Any]]],
            list[Path],
        ],
    ] = {}
    for arm_name, arm_root in (
        ("off", CORPUS_OFF_ROOT),
        ("on", CORPUS_ON_ROOT),
    ):
        key = (arm_name, fixture)
        if key not in cache:
            mapping: dict[
                str,
                tuple[int, dict[str, Any], dict[str, Any]],
            ] = {}
            transcript_path = workspace_root / _fixture_path(
                arm_root,
                fixture,
                "corrected-transcript.json",
            )
            diagnostics_path = workspace_root / _fixture_path(
                arm_root,
                fixture,
                "corrected-row-diagnostics.json",
            )
            if arm_name == "off" and not transcript_path.exists():
                _require(
                    not diagnostics_path.exists(),
                    "historical off lane is only partially absent",
                )
                paths: list[Path] = []
            else:
                segments, rows, paths = _load_fixture_lane(
                    workspace_root,
                    arm_root,
                    fixture,
                )
                for index, (segment, row) in enumerate(
                    zip(segments, rows, strict=True)
                ):
                    segment_id = segment.get("segment_id")
                    _require(
                        isinstance(segment_id, str) and segment_id,
                        "source segment ID is missing",
                    )
                    _require(
                        segment_id not in mapping,
                        "source segment IDs must be unique per fixture",
                    )
                    mapping[segment_id] = (index, segment, row)
            cache[key] = (mapping, paths)
        indexed[arm_name] = cache[key]
    off_mapping, off_paths = indexed["off"]
    on_mapping, on_paths = indexed["on"]
    return off_mapping, on_mapping, [*off_paths, *on_paths]


def classify_source_finding(
    finding: dict[str, Any],
    *,
    fixture: str,
    segment_id: str,
    proof: dict[str, Any],
) -> dict[str, Any]:
    """Classify one source alert while allowing a known-missing off lane."""
    input_classification = finding.get("classification")
    timing_and_text_aligned = (
        proof.get("timing_aligned") is True and proof.get("text_equal") is True
    )
    off_state = proof.get("off_state")
    on_state = proof.get("on_state")
    related_gate_ids: list[str] = []

    if input_classification == "ground_truth_aligned_lexical_heuristic_false_positive":
        _require(
            finding.get("correct") is True
            and finding.get("confidently_wrong") is False
            and on_state == "correct",
            "truth-aligned source finding is not mechanically correct",
        )
        adjudication = "TRUTH_ALIGNED_HEURISTIC_FALSE_POSITIVE"
        causality = "NOT_A_ROLE_ERROR"
        disposition_group = f"source-{fixture}-{segment_id}-false-positive"
    elif input_classification == "ground_truth_confirmed_role_error":
        _require(
            finding.get("correct") is False
            and finding.get("confidently_wrong") is True
            and on_state == "wrong",
            "confirmed source error is not mechanically wrong",
        )
        if timing_and_text_aligned and off_state == "correct":
            adjudication = CONFIRMED_CLASSIFICATION
            causality = "CONFIRMED"
            if fixture == C08_FIXTURE:
                disposition_group = "c08-corrected-attribution-regression"
                related_gate_ids = [
                    "c08_corrected_strict_regression",
                    "c08_incorrect_confident_regression",
                ]
            else:
                disposition_group = (
                    f"source-{fixture}-{segment_id}-candidate-regression"
                )
        elif (
            timing_and_text_aligned
            and off_state == "wrong"
            and proof.get("off_role") == proof.get("on_role")
        ):
            adjudication = "BASELINE_PERSISTENT_CONFIRMED_ROLE_ERROR"
            causality = "NOT_CANDIDATE_CAUSED"
            disposition_group = f"source-{fixture}-{segment_id}-baseline-error"
        else:
            adjudication = "UNSCORABLE_REFERENCE_GAP"
            causality = "UNVERIFIED"
            disposition_group = f"source-{fixture}-{segment_id}-unverified-error"
    elif input_classification == "unscored_overlap_or_reference_gap":
        _require(
            finding.get("correct") is None and finding.get("expected_role") is None,
            "unscored source finding unexpectedly has reference truth",
        )
        if timing_and_text_aligned and proof.get("off_role") == proof.get("on_role"):
            adjudication = "EXPLAINED_REFERENCE_GAP"
            causality = "NOT_CANDIDATE_CAUSED"
        else:
            adjudication = "UNSCORABLE_REFERENCE_GAP"
            causality = "UNVERIFIED"
        disposition_group = f"source-{fixture}-{segment_id}-reference-gap"
    else:
        raise EvidenceError("source input classification is unknown")

    return {
        "adjudication": adjudication,
        "candidate_causality": causality,
        "disposition_group": disposition_group,
        "related_legacy_gate_ids": related_gate_ids,
    }


def _build_source_matrix(
    workspace_root: Path,
) -> tuple[list[dict[str, Any]], list[Path]]:
    """Cross-map all 29 source alerts without retaining decoded wording."""
    source = _load_build_json(
        workspace_root / SOURCE_ADJUDICATION_PATH,
        "source-chip adjudication",
    )
    findings = source.get("findings")
    _require(
        isinstance(findings, list) and len(findings) == 29,
        "source-chip findings must contain 29 rows",
    )
    cache: dict[
        tuple[str, str],
        tuple[dict[str, tuple[int, dict[str, Any], dict[str, Any]]], list[Path]],
    ] = {}
    alerts: list[dict[str, Any]] = []
    source_paths: list[Path] = [workspace_root / SOURCE_ADJUDICATION_PATH]

    for alert_index, finding in enumerate(findings, start=1):
        _require(isinstance(finding, dict), "source finding must be an object")
        fixture = finding.get("fixture")
        segment_id = finding.get("segment_id")
        row_number = finding.get("row_number")
        input_classification = finding.get("classification")
        _require(isinstance(fixture, str), "source fixture must be a string")
        _require(
            isinstance(segment_id, str),
            "source segment ID must be a string",
        )
        _require(
            isinstance(row_number, int),
            "source row number must be an integer",
        )
        off_index, on_index, paths = _fixture_lane_index(
            workspace_root,
            fixture,
            cache,
        )
        source_paths.extend(paths)
        _require(segment_id in on_index, "source segment is absent from on arm")
        on_row_index, on_segment, on_diagnostic = on_index[segment_id]
        _require(
            on_row_index == row_number - 1,
            "source row number does not align to the segment",
        )

        expected_role = finding.get("expected_role")
        visible_role = finding.get("visible_role")
        _require(
            str(on_segment.get("role", "")).upper() == visible_role,
            "source visible role does not match the candidate-on segment",
        )
        _require(
            on_diagnostic.get("expected_role") == expected_role,
            "source expected role does not match candidate-on diagnostics",
        )

        if segment_id in off_index:
            off_row_index, off_segment, off_diagnostic = off_index[segment_id]
            _require(
                off_row_index == on_row_index,
                "source row indices differ between arms",
            )
            _require(
                off_diagnostic.get("expected_role") == expected_role,
                "source expected role differs between arms",
            )
            proof = _aligned_row_proof(
                off_segment,
                on_segment,
                expected_role,
                on_row_index,
            )
            proof.pop("row_index")
            proof.pop("row_number")
        else:
            proof = {
                "end": float(on_segment.get("end", 0.0)),
                "expected_role": expected_role,
                "off_role": None,
                "off_segment_id": None,
                "off_state": "unavailable",
                "off_text_sha256": None,
                "on_role": str(on_segment.get("role", "")).upper(),
                "on_segment_id": str(on_segment.get("segment_id", "")),
                "on_state": _attribution_state(
                    on_segment.get("role"),
                    expected_role,
                ),
                "on_text_sha256": text_sha256(on_segment.get("text", "")),
                "start": float(on_segment.get("start", 0.0)),
                "text_equal": None,
                "timing_aligned": None,
            }
        classification = classify_source_finding(
            finding,
            fixture=fixture,
            segment_id=segment_id,
            proof=proof,
        )

        codes = finding.get("codes")
        _require(
            isinstance(codes, list)
            and codes
            and all(
                isinstance(code, str) and re.fullmatch(r"[a-z0-9_]+", code) is not None
                for code in codes
            ),
            "source heuristic codes are malformed",
        )
        alerts.append(
            {
                "adjudication": classification["adjudication"],
                "alert_id": f"source-alert-{alert_index:02d}",
                "candidate_causality": classification["candidate_causality"],
                "codes": codes,
                "disposition_group": classification["disposition_group"],
                "fixture": fixture,
                "input_classification": input_classification,
                "proof": proof,
                "related_legacy_gate_ids": classification["related_legacy_gate_ids"],
                "row_number": row_number,
                "segment_id": segment_id,
            }
        )

    unique_paths = list(dict.fromkeys(source_paths))
    return alerts, unique_paths


def _build_gate_matrix(
    workspace_root: Path,
) -> tuple[list[dict[str, Any]], list[Path]]:
    """Build all seven legacy classifications from the frozen evidence."""
    c03_gates, c03_paths = _build_c03_gates(workspace_root)
    c08_gates, c08_paths = _build_c08_gates(workspace_root)
    historical_gates, historical_paths = _build_historical_gates(workspace_root)
    gates = [*c03_gates, *c08_gates, *historical_gates]
    _require(
        [gate["gate_id"] for gate in gates] == list(EXPECTED_LEGACY_GATE_IDS),
        "generated gate order does not match the seven-gate contract",
    )
    return gates, list(dict.fromkeys([*c03_paths, *c08_paths, *historical_paths]))


def _source_identity_paths(
    workspace_root: Path,
    consumed_paths: list[Path],
) -> list[Path]:
    """Return every external input whose bytes affect the packet."""
    paths = [
        Path(__file__).resolve(),
        workspace_root / LEGACY_VERIFIER_PATH,
        workspace_root / LEGACY_ADJUDICATION_PATH,
        workspace_root / SOURCE_ADJUDICATION_PATH,
        workspace_root / ARM_VERIFICATION_PATH,
        workspace_root / M05B_ATTESTATION_PATH,
        workspace_root / CORPUS_ON_ROOT / "artifact-manifest.sha256",
        workspace_root / M05B_PACKET_ROOT / "packet-manifest.sha256",
        workspace_root / M05B_VERIFICATION_ROOT / "artifact-manifest.sha256",
        *(workspace_root / raw_path for raw_path in FROZEN_SOURCE_HASHES),
        *consumed_paths,
    ]
    unique_paths = list(dict.fromkeys(path.resolve() for path in paths))
    for path in unique_paths:
        _require(path.is_file(), "a consumed source identity file is missing")
    return unique_paths


def _source_identities_document(
    workspace_root: Path,
    paths: list[Path],
) -> dict[str, Any]:
    """Bind every consumed source with a deterministic generated label."""
    records = []
    for index, path in enumerate(paths, start=1):
        relative = path.relative_to(workspace_root.resolve()).as_posix()
        label_slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", relative).strip("-")
        records.append(
            _evidence_record(
                workspace_root,
                path,
                label=f"source-{index:02d}-{label_slug}",
            )
        )
    return {
        "records": records,
        "schema_version": SOURCE_IDENTITIES_SCHEMA_VERSION,
    }


def _validate_build_documents(
    gates: list[dict[str, Any]],
    alerts: list[dict[str, Any]],
    source_identities: dict[str, Any],
    receipts: dict[str, Any],
) -> str:
    """Run semantic validation before creating the evidence root."""
    result = VerificationResult()
    _verify_no_wording_keys(
        result,
        {"gates": gates},
        "generated gate matrix",
    )
    _verify_no_wording_keys(
        result,
        {"alerts": alerts},
        "generated source-chip matrix",
    )
    validated_gates = _validate_gate_matrix(
        result,
        {
            "gates": gates,
            "schema_version": GATE_MATRIX_SCHEMA_VERSION,
        },
    )
    validated_alerts = _validate_source_matrix(
        result,
        {
            "alerts": alerts,
            "schema_version": SOURCE_MATRIX_SCHEMA_VERSION,
        },
    )
    _validate_receipts(result, receipts)
    if source_identities.get(
        "schema_version"
    ) != SOURCE_IDENTITIES_SCHEMA_VERSION or not source_identities.get("records"):
        result.fail("generated source identities are invalid")
    if result.failures:
        raise EvidenceError("; ".join(result.failures))
    return derive_disposition(validated_gates, validated_alerts)


def _decision_markdown(disposition: str) -> str:
    """Render a narrow, wording-free operator decision."""
    return "\n".join(
        [
            "# M05D candidate disposition",
            "",
            f"Disposition: {disposition}",
            "Promotion authorized: false.",
            "Full campaign pass: false.",
            "Runtime calls added: 0.",
            "",
            "The sealed seven-gate and 29-alert matrices are authoritative.",
            "Aggregate improvement does not waive a row or fixture failure.",
            "Any later documentation or comparison work requires separate approval.",
            "",
        ]
    )


def _write_inventory(packet_root: Path, paths: list[Path]) -> Path:
    """Write the core-artifact inventory, excluding itself and the manifest."""
    inventory_path = packet_root / "artifact-inventory.txt"
    lines = ["path\tbytes\tsha256"]
    lines.extend(
        (
            f"{path.relative_to(packet_root).as_posix()}\t"
            f"{path.stat().st_size}\t{file_sha256(path)}"
        )
        for path in sorted(paths)
    )
    inventory_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return inventory_path


def _write_manifest(packet_root: Path, paths: list[Path]) -> Path:
    """Write the final manifest without scanning or hashing itself."""
    manifest_path = packet_root / "artifact-manifest.sha256"
    lines = [
        f"{file_sha256(path)}  ./{path.relative_to(packet_root).as_posix()}"
        for path in sorted(paths)
    ]
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest_path


def build_quality_packet(
    workspace_root: Path,
    packet_root: Path,
    receipts_path: Path,
) -> VerificationResult:
    """Build, manifest, seal, and reverify one M05D decision packet."""
    resolved_root = workspace_root.resolve()
    resolved_packet = (
        packet_root if packet_root.is_absolute() else resolved_root / packet_root
    ).resolve()
    try:
        resolved_packet.relative_to(resolved_root)
    except ValueError as error:
        raise EvidenceError("packet root must stay inside the workspace") from error
    _require(not resolved_packet.exists(), "packet root already exists")

    _verify_frozen_inputs(resolved_root)
    gates, gate_paths = _build_gate_matrix(resolved_root)
    alerts, source_paths = _build_source_matrix(resolved_root)
    consumed_paths = list(dict.fromkeys([*gate_paths, *source_paths]))
    source_identity_paths = _source_identity_paths(
        resolved_root,
        consumed_paths,
    )
    source_identities = _source_identities_document(
        resolved_root,
        source_identity_paths,
    )
    receipts = _load_build_json(
        receipts_path.resolve(),
        "verification receipts",
    )
    disposition = _validate_build_documents(
        gates,
        alerts,
        source_identities,
        receipts,
    )

    resolved_packet.mkdir(parents=True)
    gate_matrix_path = resolved_packet / "gate-matrix.json"
    write_json(
        gate_matrix_path,
        {
            "gates": gates,
            "schema_version": GATE_MATRIX_SCHEMA_VERSION,
        },
    )
    source_matrix_path = resolved_packet / "source-chip-matrix.json"
    write_json(
        source_matrix_path,
        {
            "alerts": alerts,
            "schema_version": SOURCE_MATRIX_SCHEMA_VERSION,
        },
    )
    source_identities_path = resolved_packet / "source-identities.json"
    write_json(source_identities_path, source_identities)
    verification_receipts_path = resolved_packet / "verification-receipts.json"
    write_json(verification_receipts_path, receipts)
    decision_markdown_path = resolved_packet / "decision.md"
    decision_markdown_path.write_text(
        _decision_markdown(disposition),
        encoding="utf-8",
    )

    decision_path = resolved_packet / "decision.json"
    write_json(
        decision_path,
        {
            "claim_scope": "sealed CPU-only quality adjudication",
            "decision_markdown": _evidence_record(
                resolved_root,
                decision_markdown_path,
            ),
            "disposition": disposition,
            "full_campaign_pass": False,
            "gate_matrix": _evidence_record(
                resolved_root,
                gate_matrix_path,
            ),
            "legacy_gate_count": 7,
            "packet_manifest_path": (resolved_packet / "artifact-manifest.sha256")
            .relative_to(resolved_root)
            .as_posix(),
            "promotion_authorized": False,
            "runtime_calls_added": 0,
            "schema_version": DECISION_SCHEMA_VERSION,
            "source_chip_alert_count": 29,
            "source_chip_matrix": _evidence_record(
                resolved_root,
                source_matrix_path,
            ),
            "source_identities": _evidence_record(
                resolved_root,
                source_identities_path,
            ),
            "verification_receipts": _evidence_record(
                resolved_root,
                verification_receipts_path,
            ),
        },
    )
    verifier_result_path = resolved_packet / "verifier-result.json"
    write_json(
        verifier_result_path,
        {
            "disposition": disposition,
            "full_campaign_pass": False,
            "legacy_gate_count": 7,
            "ok": True,
            "phase": "preseal",
            "promotion_authorized": False,
            "schema_version": RESULT_SCHEMA_VERSION,
            "source_chip_alert_count": 29,
        },
    )

    core_paths = [
        decision_path,
        decision_markdown_path,
        gate_matrix_path,
        source_matrix_path,
        source_identities_path,
        verification_receipts_path,
        verifier_result_path,
    ]
    inventory_path = _write_inventory(resolved_packet, core_paths)
    manifest_path = _write_manifest(
        resolved_packet,
        [*core_paths, inventory_path],
    )
    for path in [*core_paths, inventory_path, manifest_path]:
        path.chmod(0o400)
    resolved_packet.chmod(0o500)

    return verify_decision_packet(
        resolved_root,
        decision_path,
        require_read_only=True,
    )


def _parser() -> argparse.ArgumentParser:
    """Create the bounded build/verify CLI."""
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
        help="verify an existing sealed M05D packet",
    )
    verify_parser.add_argument("--packet", type=Path, required=True)

    build_parser = subparsers.add_parser(
        "build",
        help="build and seal the CPU-only M05D packet",
    )
    build_parser.add_argument(
        "--packet-root",
        type=Path,
        default=DEFAULT_PACKET_ROOT,
    )
    build_parser.add_argument(
        "--verification-receipts",
        type=Path,
        required=True,
    )
    return parser


def main() -> int:
    """Run one filesystem-only build or verification."""
    arguments = _parser().parse_args()
    workspace_root = arguments.repo_root.resolve()
    try:
        if arguments.command == "build":
            result = build_quality_packet(
                workspace_root,
                arguments.packet_root,
                arguments.verification_receipts,
            )
        else:
            packet = arguments.packet
            if not packet.is_absolute():
                packet = workspace_root / packet
            result = verify_decision_packet(workspace_root, packet)
    except EvidenceError as error:
        result = VerificationResult()
        result.fail(str(error))
    print(json.dumps(result.as_document(), indent=2, sort_keys=True))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
