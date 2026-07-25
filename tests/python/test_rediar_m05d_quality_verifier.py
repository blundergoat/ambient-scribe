"""Fail-closed contracts for the M05D corpus-quality adjudicator."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFIER_PATH = REPO_ROOT / "scripts" / "verify-rediar-m05d-quality.py"

verifier_spec = importlib.util.spec_from_file_location(
    "verify_rediar_m05d_quality",
    VERIFIER_PATH,
)
assert verifier_spec is not None
quality_verifier = importlib.util.module_from_spec(verifier_spec)
assert verifier_spec.loader is not None
sys.modules[verifier_spec.name] = quality_verifier
verifier_spec.loader.exec_module(quality_verifier)


def file_sha256(path: Path) -> str:
    """Return the exact digest used by synthetic evidence records."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, document: Any) -> None:
    """Write stable JSON so packet hashes are deterministic."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def evidence_record(workspace_root: Path, path: Path) -> dict[str, object]:
    """Build one root-relative byte/hash binding."""
    return {
        "path": path.relative_to(workspace_root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": file_sha256(path),
    }


def write_inventory(packet_root: Path, paths: list[Path]) -> Path:
    """Write the non-self-referential packet inventory."""
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


def write_manifest(packet_root: Path, paths: list[Path]) -> Path:
    """Write a GNU-style manifest excluding the manifest itself."""
    manifest_path = packet_root / "artifact-manifest.sha256"
    lines = [
        f"{file_sha256(path)}  ./{path.relative_to(packet_root).as_posix()}"
        for path in sorted(paths)
    ]
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest_path


def synthetic_gate(
    gate_id: str,
    *,
    classification: str,
    candidate_causality: str,
    disposition_group: str,
) -> dict[str, object]:
    """Create one sanitized legacy-gate classification."""
    return {
        "blocking_effect": (
            "REJECT"
            if classification == "CONFIRMED_SUBSTANTIVE_CANDIDATE_REGRESSION"
            else "NONE"
        ),
        "candidate_causality": candidate_causality,
        "classification": classification,
        "disposition_group": disposition_group,
        "disposition_trigger": (
            classification == "CONFIRMED_SUBSTANTIVE_CANDIDATE_REGRESSION"
        ),
        "gate_id": gate_id,
        "legacy_failure_class": "synthetic_legacy_failure",
        "proof": {
            "expected_role": "DOCTOR",
            "off_role": "DOCTOR",
            "on_role": (
                "PATIENT"
                if classification == "CONFIRMED_SUBSTANTIVE_CANDIDATE_REGRESSION"
                else "DOCTOR"
            ),
            "off_text_sha256": "1" * 64,
            "on_text_sha256": "1" * 64,
            "row_index": 1,
            "segment_id": "corrected-0002",
            "text_equal": True,
            "timing_aligned": True,
        },
        "proof_class": "SYNTHETIC_MECHANICAL",
    }


def synthetic_gates(
    disposition: str,
    *,
    missing_gate: bool = False,
    wording_leak: bool = False,
) -> list[dict[str, object]]:
    """Create the exact seven-gate matrix for one decision branch."""
    gates: list[dict[str, object]] = []
    for index, gate_id in enumerate(quality_verifier.EXPECTED_LEGACY_GATE_IDS):
        if gate_id.startswith("historical_"):
            classification = "EXPECTED_HISTORICAL_GAP"
            causality = "NOT_CANDIDATE_ON"
        elif disposition == "REJECT_CANDIDATE_KEEP_FLAG_OFF" and index == 0:
            classification = "CONFIRMED_SUBSTANTIVE_CANDIDATE_REGRESSION"
            causality = "CONFIRMED"
        elif disposition == "BLOCKED_UNVERIFIED" and index == 0:
            classification = "UNVERIFIED_COMPARISON_ALIGNMENT"
            causality = "UNVERIFIED"
        else:
            classification = "CLEARED_NO_SUBSTANTIVE_FAILURE"
            causality = "NOT_CONFIRMED"
        gates.append(
            synthetic_gate(
                gate_id,
                classification=classification,
                candidate_causality=causality,
                disposition_group=f"synthetic-group-{index}",
            )
        )

    if missing_gate:
        gates.pop()
    if wording_leak:
        gates[0]["proof"]["text"] = "synthetic clinical wording"
    return gates


def synthetic_source_alerts(
    disposition: str,
    *,
    duplicate_alert: bool = False,
) -> list[dict[str, object]]:
    """Create 29 unique alerts with the frozen 22/3/4 input split."""
    alerts: list[dict[str, object]] = []
    for index in range(29):
        if index < 22:
            input_classification = (
                "ground_truth_aligned_lexical_heuristic_false_positive"
            )
            adjudication = "TRUTH_ALIGNED_HEURISTIC_FALSE_POSITIVE"
            causality = "NOT_A_ROLE_ERROR"
        elif index < 25:
            input_classification = "ground_truth_confirmed_role_error"
            adjudication = "BASELINE_PERSISTENT_CONFIRMED_ROLE_ERROR"
            causality = "NOT_CANDIDATE_CAUSED"
        else:
            input_classification = "unscored_overlap_or_reference_gap"
            adjudication = "EXPLAINED_REFERENCE_GAP"
            causality = "NOT_CANDIDATE_CAUSED"

        if disposition == "REJECT_CANDIDATE_KEEP_FLAG_OFF" and index == 24:
            adjudication = "CONFIRMED_SUBSTANTIVE_CANDIDATE_REGRESSION"
            causality = "CONFIRMED"
        elif disposition == "REJECT_CANDIDATE_KEEP_FLAG_OFF" and index in {25, 28}:
            adjudication = "UNSCORABLE_REFERENCE_GAP"
            causality = "UNVERIFIED"
        elif disposition == "BLOCKED_UNVERIFIED" and index == 25:
            adjudication = "UNSCORABLE_REFERENCE_GAP"
            causality = "UNVERIFIED"

        alert = {
            "adjudication": adjudication,
            "alert_id": f"source-alert-{index + 1:02d}",
            "candidate_causality": causality,
            "codes": ["synthetic_heuristic"],
            "disposition_group": f"source-group-{index}",
            "fixture": f"synthetic-fixture-{index:02d}",
            "input_classification": input_classification,
            "proof": {
                "expected_role": (
                    None
                    if input_classification == "unscored_overlap_or_reference_gap"
                    else "DOCTOR"
                ),
                "off_role": "DOCTOR",
                "on_role": (
                    "PATIENT" if causality in {"CONFIRMED", "UNVERIFIED"} else "DOCTOR"
                ),
                "off_text_sha256": "2" * 64,
                "on_text_sha256": "2" * 64,
                "start": float(index),
                "end": float(index) + 0.5,
                "text_equal": True,
                "timing_aligned": True,
            },
            "related_legacy_gate_ids": [],
            "row_number": index + 1,
            "segment_id": f"corrected-{index + 1:04d}",
        }
        alerts.append(alert)

    if duplicate_alert:
        duplicate = deepcopy(alerts[-1])
        duplicate["alert_id"] = "source-alert-duplicate"
        alerts[-1] = duplicate
        alerts[-1]["fixture"] = alerts[-2]["fixture"]
        alerts[-1]["row_number"] = alerts[-2]["row_number"]
        alerts[-1]["segment_id"] = alerts[-2]["segment_id"]
    return alerts


def build_synthetic_packet(
    tmp_path: Path,
    *,
    disposition: str = "REJECT_CANDIDATE_KEEP_FLAG_OFF",
    missing_gate: bool = False,
    duplicate_alert: bool = False,
    wording_leak: bool = False,
    promotion_authorized: bool = False,
    full_campaign_pass: bool = False,
    manifest_drift: bool = False,
    source_drift: bool = False,
    writable_packet: bool = False,
) -> tuple[Path, Path]:
    """Build one isolated M05D packet with optional fail-closed mutations."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    packet_root = workspace_root / "var/quality/m05d-test-packet"
    packet_root.mkdir(parents=True)

    frozen_input = workspace_root / "inputs/frozen-input.json"
    write_json(frozen_input, {"schema_version": "synthetic-frozen-input/v1"})
    source_identities_path = packet_root / "source-identities.json"
    write_json(
        source_identities_path,
        {
            "records": [
                {
                    "label": "synthetic_frozen_input",
                    **evidence_record(workspace_root, frozen_input),
                }
            ],
            "schema_version": quality_verifier.SOURCE_IDENTITIES_SCHEMA_VERSION,
        },
    )

    gates = synthetic_gates(
        disposition,
        missing_gate=missing_gate,
        wording_leak=wording_leak,
    )
    gate_matrix_path = packet_root / "gate-matrix.json"
    write_json(
        gate_matrix_path,
        {
            "gates": gates,
            "schema_version": quality_verifier.GATE_MATRIX_SCHEMA_VERSION,
        },
    )

    alerts = synthetic_source_alerts(
        disposition,
        duplicate_alert=duplicate_alert,
    )
    source_matrix_path = packet_root / "source-chip-matrix.json"
    write_json(
        source_matrix_path,
        {
            "alerts": alerts,
            "schema_version": quality_verifier.SOURCE_MATRIX_SCHEMA_VERSION,
        },
    )

    receipts_path = packet_root / "verification-receipts.json"
    write_json(
        receipts_path,
        {
            "commands": [
                {"exit_code": 0, "name": name}
                for name in ("ruff_check", "ruff_format", "py_compile", "pytest")
            ],
            "schema_version": quality_verifier.RECEIPTS_SCHEMA_VERSION,
        },
    )

    decision_markdown_path = packet_root / "decision.md"
    decision_markdown_path.write_text(
        "\n".join(
            [
                "# M05D candidate disposition",
                "",
                f"Disposition: {disposition}",
                "Promotion authorized: false.",
                "Full campaign pass: false.",
                "Runtime calls added: 0.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    decision_path = packet_root / "decision.json"
    write_json(
        decision_path,
        {
            "claim_scope": "sealed CPU-only quality adjudication",
            "decision_markdown": evidence_record(
                workspace_root,
                decision_markdown_path,
            ),
            "disposition": disposition,
            "full_campaign_pass": full_campaign_pass,
            "gate_matrix": evidence_record(workspace_root, gate_matrix_path),
            "legacy_gate_count": 7,
            "packet_manifest_path": (packet_root / "artifact-manifest.sha256")
            .relative_to(workspace_root)
            .as_posix(),
            "promotion_authorized": promotion_authorized,
            "runtime_calls_added": 0,
            "schema_version": quality_verifier.DECISION_SCHEMA_VERSION,
            "source_chip_alert_count": 29,
            "source_chip_matrix": evidence_record(
                workspace_root,
                source_matrix_path,
            ),
            "source_identities": evidence_record(
                workspace_root,
                source_identities_path,
            ),
            "verification_receipts": evidence_record(
                workspace_root,
                receipts_path,
            ),
        },
    )

    verifier_result_path = packet_root / "verifier-result.json"
    write_json(
        verifier_result_path,
        {
            "disposition": disposition,
            "full_campaign_pass": False,
            "legacy_gate_count": 7,
            "ok": True,
            "phase": "preseal",
            "promotion_authorized": False,
            "schema_version": quality_verifier.RESULT_SCHEMA_VERSION,
            "source_chip_alert_count": 29,
        },
    )

    core_paths = [
        decision_path,
        decision_markdown_path,
        gate_matrix_path,
        source_matrix_path,
        source_identities_path,
        receipts_path,
        verifier_result_path,
    ]
    inventory_path = write_inventory(packet_root, core_paths)
    write_manifest(packet_root, [*core_paths, inventory_path])

    if manifest_drift:
        gate_matrix_path.write_text(
            gate_matrix_path.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
    if source_drift:
        write_json(
            frozen_input,
            {"schema_version": "synthetic-frozen-input/changed"},
        )

    frozen_input.chmod(0o444)
    for packet_path in packet_root.iterdir():
        packet_path.chmod(0o400)
    packet_root.chmod(0o500)

    if writable_packet:
        packet_root.chmod(0o700)
        decision_path.chmod(0o600)

    return workspace_root, decision_path


def verify_synthetic_packet(
    tmp_path: Path,
    **options: object,
) -> Any:
    """Build and verify one isolated packet variant."""
    workspace_root, decision_path = build_synthetic_packet(
        tmp_path,
        **options,
    )
    return quality_verifier.verify_decision_packet(
        workspace_root,
        decision_path,
    )


def test_valid_rejection_packet_passes_without_promoting(
    tmp_path: Path,
) -> None:
    """One confirmed candidate regression emits only the rejection disposition."""
    result = verify_synthetic_packet(tmp_path)

    assert result.ok is True
    assert result.disposition == "REJECT_CANDIDATE_KEEP_FLAG_OFF"
    assert result.promotion_authorized is False
    assert result.full_campaign_pass is False
    assert result.failures == []


def test_historical_gaps_alone_allow_only_separate_comparison_eligibility(
    tmp_path: Path,
) -> None:
    """Explained historical gaps cannot become a full-campaign pass."""
    result = verify_synthetic_packet(
        tmp_path,
        disposition="ELIGIBLE_FOR_SEPARATE_SUPPLEMENTAL_COMPARISON_APPROVAL",
    )

    assert result.ok is True
    assert (
        result.disposition == "ELIGIBLE_FOR_SEPARATE_SUPPLEMENTAL_COMPARISON_APPROVAL"
    )
    assert result.promotion_authorized is False
    assert result.full_campaign_pass is False


def test_unverified_evidence_blocks_when_no_regression_is_confirmed(
    tmp_path: Path,
) -> None:
    """Unverified evidence is blocking unless rejection is already proved."""
    result = verify_synthetic_packet(
        tmp_path,
        disposition="BLOCKED_UNVERIFIED",
    )

    assert result.ok is True
    assert result.disposition == "BLOCKED_UNVERIFIED"
    assert result.promotion_authorized is False
    assert result.full_campaign_pass is False


def test_truth_aligned_alert_does_not_require_historical_off_lane() -> None:
    """Candidate-on truth can clear a heuristic alert when off is unavailable."""
    classification = quality_verifier.classify_source_finding(
        {
            "classification": ("ground_truth_aligned_lexical_heuristic_false_positive"),
            "confidently_wrong": False,
            "correct": True,
        },
        fixture="historical-fixture",
        segment_id="corrected-0001",
        proof={
            "off_role": None,
            "off_state": "unavailable",
            "on_role": "DOCTOR",
            "on_state": "correct",
            "text_equal": None,
            "timing_aligned": None,
        },
    )

    assert classification == {
        "adjudication": "TRUTH_ALIGNED_HEURISTIC_FALSE_POSITIVE",
        "candidate_causality": "NOT_A_ROLE_ERROR",
        "disposition_group": (
            "source-historical-fixture-corrected-0001-false-positive"
        ),
        "related_legacy_gate_ids": [],
    }


@pytest.mark.parametrize(
    ("options", "failure_fragment"),
    [
        ({"manifest_drift": True}, "manifest SHA-256 mismatch"),
        ({"source_drift": True}, "source identity SHA-256 mismatch"),
        ({"missing_gate": True}, "legacy gate IDs"),
        ({"duplicate_alert": True}, "source alert identities must be unique"),
        ({"wording_leak": True}, "forbidden decoded-wording key"),
        ({"promotion_authorized": True}, "promotion_authorized must be false"),
        ({"full_campaign_pass": True}, "full_campaign_pass must be false"),
        ({"writable_packet": True}, "must be read-only"),
    ],
)
def test_packet_mutations_fail_closed(
    tmp_path: Path,
    options: dict[str, object],
    failure_fragment: str,
) -> None:
    """Hash, count, privacy, scope, and permission mutations all reject."""
    result = verify_synthetic_packet(tmp_path, **options)

    assert result.ok is False
    assert any(failure_fragment in failure for failure in result.failures)
