"""Fail-closed contracts for the M05 hybrid flag-off evidence verifier."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFIER_PATH = REPO_ROOT / "scripts" / "verify-rediar-m05-hybrid.py"

verifier_spec = importlib.util.spec_from_file_location(
    "verify_rediar_m05_hybrid",
    VERIFIER_PATH,
)
assert verifier_spec is not None
hybrid_verifier = importlib.util.module_from_spec(verifier_spec)
assert verifier_spec.loader is not None
sys.modules[verifier_spec.name] = hybrid_verifier
verifier_spec.loader.exec_module(hybrid_verifier)


FIXTURE_DURATIONS = (
    (
        "primock57-day1-consultation02-i-have-sore-red-skin",
        559.2,
        4,
    ),
    (
        "primock57-day1-consultation03-i-have-terrible-headache",
        544.3,
        3,
    ),
    (
        "primock57-day1-consultation06-hard-to-breathe",
        662.5,
        4,
    ),
    (
        "primock57-day1-consultation07-i-have-a-cough-and-cold",
        858.2,
        5,
    ),
    (
        "primock57-day1-consultation08-i-have-dry-itchy-skin",
        466.2,
        3,
    ),
    (
        "primock57-day2-consultation03-i-cant-hear-very-well-and-my-face-is-a-bit-numb",
        450.8,
        3,
    ),
    (
        "primock57-day2-consultation09-i-cant-move-my-left-arm",
        432.24,
        3,
    ),
    (
        "primock57-day3-consultation01-lips-swelling-after-eating-a-sandwich",
        510.1,
        3,
    ),
    (
        "primock57-day5-consultation03-im-feeling-very-anxious",
        776.3,
        5,
    ),
    (
        "primock57-day5-consultation09-tired-all-the-time",
        579.0,
        4,
    ),
)


def file_sha256(path: Path) -> str:
    """Return the exact digest used by synthetic evidence records."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, document: Any) -> None:
    """Write stable JSON so test hashes are deterministic."""
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


def write_sha_manifest(root: Path, paths: list[Path], manifest_path: Path) -> None:
    """Write a GNU-style SHA manifest over root-confined files."""
    records = [
        f"{file_sha256(path)}  ./{path.relative_to(root).as_posix()}"
        for path in sorted(paths)
    ]
    manifest_path.write_text("\n".join(records) + "\n", encoding="utf-8")


def build_synthetic_hybrid_workspace(
    tmp_path: Path,
    *,
    corpus_on_status: str = "NOT_EVALUATED",
    malformed_d3_hash: bool = False,
    missing_replay_proof: bool = False,
    physical_calls: int = 4,
    fifth_call_attempted: bool = False,
    identity_clean: bool = True,
    mutate_source_after_manifest: bool = False,
) -> tuple[Path, Path]:
    """Create a sealed, internally hash-consistent hybrid packet."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    d3_paths = tuple(Path(path) for path in hybrid_verifier.D3_DELTA_PATHS)
    for index, relative_path in enumerate(d3_paths, start=1):
        source_path = workspace_root / relative_path
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(f"d3 file {index}\n", encoding="utf-8")

    packet_root = (
        workspace_root / "var/quality/rediar-m05-acceptance/hybrid/2026-07-25-test"
    )
    packet_root.mkdir(parents=True)
    d3_manifest_path = packet_root / "d3-delta-manifest.tsv"
    d3_lines = ["path\tbytes\tsha256"]
    for index, relative_path in enumerate(d3_paths):
        source_path = workspace_root / relative_path
        digest = (
            "not-a-sha256"
            if malformed_d3_hash and index == 0
            else file_sha256(source_path)
        )
        d3_lines.append(
            f"{relative_path.as_posix()}\t{source_path.stat().st_size}\t{digest}"
        )
    d3_manifest_path.write_text("\n".join(d3_lines) + "\n", encoding="utf-8")

    identity_receipts: list[dict[str, object]] = []
    for arm in hybrid_verifier.HISTORICAL_IDENTITY_ARMS:
        receipt_path = (
            workspace_root
            / f"var/quality/rediar-m05-acceptance/arms/{arm}/identity-check.txt"
        )
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        clean_marker = (
            "candidate-surface: clean"
            if identity_clean
            else ("candidate-surface: dirty")
        )
        receipt_path.write_text(
            "\n".join(
                [
                    f"arm={arm} checked_at=2026-07-24T00:00:00Z",
                    f"freeze_sha={hybrid_verifier.FREEZE_SHA}",
                    f"head_sha={hybrid_verifier.FREEZE_SHA}",
                    clean_marker,
                    "development-corpus: valid",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        identity_receipts.append(
            {
                "arm": arm,
                **evidence_record(workspace_root, receipt_path),
            }
        )

    historical_corpus_receipts: list[dict[str, object]] = []
    for fixture, _duration, chunks in FIXTURE_DURATIONS:
        if fixture == hybrid_verifier.REPLACEMENT_FIXTURE:
            continue
        response_path = (
            workspace_root
            / "var/quality/rediar-m05-acceptance/arms/corpus-off/corpus"
            / fixture
            / "correction-response.json"
        )
        write_json(
            response_path,
            {
                "attempts": 1,
                "chunk_count": chunks,
                "retried": False,
                "source_state": "whole_visit_corrected",
                "status": "ready",
            },
        )
        historical_corpus_receipts.append(
            {
                "fixture": fixture,
                "expected_chunks": chunks,
                **evidence_record(workspace_root, response_path),
            }
        )

    replay_root = (
        workspace_root
        / "var/quality/rediar-m05-acceptance/diagnostics"
        / "2026-07-25_d2c09-d3-recovery-only-replay1"
    )
    replay_root.mkdir(parents=True)
    result_summary_path = replay_root / "result-summary.json"
    write_json(
        result_summary_path,
        {
            "attempts": 2,
            "chunk_count": 3,
            "claim_scope": (
                "flag-off recovery availability only; corpus-on and promotion "
                "not evaluated"
            ),
            "correction_status": "ready",
            "fixture": hybrid_verifier.REPLACEMENT_FIXTURE,
            "normal_runtime_restored": True,
            "physical_correction_calls": physical_calls,
            "punctuation_timing_folds": 1,
            "rediarization_flag": 0,
            "replays": 1,
            "retried": True,
            "status": ("pass_with_nonblocking_truth_aligned_scorer_findings"),
            "word_confidence_fallbacks": 1,
        },
    )

    diagnostic_path = replay_root / "diagnostic-verification-final.json"
    if not missing_replay_proof:
        write_json(
            diagnostic_path,
            {
                "checks": {
                    "eval_exit_zero": True,
                    "flag_off_no_rediarization": True,
                    "normal_runtime_restored": True,
                    "physical_call_count_four": physical_calls == 4,
                    "single_exact_recovery": True,
                    "source_hashes_unchanged": True,
                },
                "claim_scope": (
                    "replacement flag-off recovery replay only; corpus-on and "
                    "promotion not evaluated"
                ),
                "counts": {
                    "model_restores": 1,
                    "physical_correction_calls": physical_calls,
                    "punctuation_timing_folds": 1,
                    "word_confidence_fallbacks": 1,
                },
                "spend_after": {
                    "corpus_on_calls": 0,
                    "physical_correction_calls": "72/95",
                    "replays": "31/41",
                },
                "status": ("pass_with_nonblocking_truth_aligned_scorer_findings"),
            },
        )

    call_ledger_path = replay_root / "call-ledger.txt"
    call_ledger_path.write_text(
        "\n".join(
            [
                "approved_replay_cap=1",
                "actual_replays=1",
                "planned_chunks=3",
                "approved_physical_correction_call_cap=4",
                f"actual_physical_correction_calls={physical_calls}",
                "model_restores=1",
                "word_confidence_fallback_events=1",
                "punctuation_timing_fold_events=1 input_rows=214 "
                "output_words=213 folds=1",
                f"fifth_call_attempted={str(fifth_call_attempted).lower()}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    run_identity_path = replay_root / "run-identity.txt"
    run_identity_path.write_text(
        "\n".join(
            [
                "diagnostic=d2c09-d3-recovery-only-replay1",
                "flag=0",
                f"fixture={hybrid_verifier.REPLACEMENT_FIXTURE}",
                "pace=1x",
                "chunk_ms=5000",
                "replay_cap=1",
                "physical_correction_call_cap=4",
                "role_call_cap=80",
                "corpus_on_cap=0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    replay_files = [
        result_summary_path,
        call_ledger_path,
        run_identity_path,
    ]
    if diagnostic_path.exists():
        replay_files.append(diagnostic_path)
    filler_count = hybrid_verifier.REPLAY_MANIFEST_RECORDS - len(replay_files)
    for filler_index in range(filler_count):
        filler_path = replay_root / "synthetic" / f"proof-{filler_index:03d}.txt"
        filler_path.parent.mkdir(parents=True, exist_ok=True)
        filler_path.write_text(f"proof {filler_index}\n", encoding="utf-8")
        replay_files.append(filler_path)

    replay_manifest_path = replay_root / "artifact-manifest.sha256"
    write_sha_manifest(replay_root, replay_files, replay_manifest_path)

    development_manifest_path = (
        workspace_root / "tests/fixtures/audio/development-corpus-0.5.0.json"
    )
    write_json(
        development_manifest_path,
        {"schema_version": "ambient-scribe-development-corpus/v1"},
    )

    cap_packet_path = packet_root / "future-cap-packet.json"
    write_json(
        cap_packet_path,
        {
            "authorization": "NOT_AUTHORIZED",
            "base_transcribe_calls": 37,
            "calculation": {
                "audio_chunk_seconds": 180.0,
                "engine": ("strands_agents.post_visit_correction._build_audio_chunks"),
                "minimum_final_chunk_seconds": 10.0,
                "one_shot_max_audio_seconds": 240.0,
                "synthetic_sample_rate_hz": 100,
            },
            "conservative_future_arm_calls": 47,
            "conservative_recovery_calls": 10,
            "conservative_total_physical_calls": 119,
            "corpus_on_status": "NOT_EVALUATED",
            "current_physical_calls": 72,
            "development_manifest": evidence_record(
                workspace_root,
                development_manifest_path,
            ),
            "existing_approved_call_cap": 95,
            "expected_future_arm_calls": 38,
            "expected_recovery_calls": 1,
            "expected_total_physical_calls": 110,
            "fixtures": [
                {
                    "fixture": fixture,
                    "duration_seconds": duration,
                    "production_chunks": chunks,
                }
                for fixture, duration, chunks in FIXTURE_DURATIONS
            ],
            "production_chunker": evidence_record(
                workspace_root,
                workspace_root / "strands_agents/post_visit_correction.py",
            ),
            "requires_new_approval": True,
            "schema_version": hybrid_verifier.CAP_SCHEMA_VERSION,
        },
    )

    decision_path = packet_root / "decision.md"
    decision_path.write_text(
        "\n".join(
            [
                "# M05B hybrid flag-off decision",
                "",
                "Verdict: PASS_HYBRID_FLAG_OFF",
                "Claim: flag-off recovery availability/correctness only.",
                "Corpus-on: NOT_EVALUATED.",
                "Full campaign: NOT_EVALUATED.",
                "Promotion/default flip: NOT_AUTHORIZED.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    synthetic_verifier_path = workspace_root / "scripts/verify-rediar-m05-hybrid.py"
    synthetic_verifier_path.parent.mkdir(parents=True, exist_ok=True)
    synthetic_verifier_path.write_text("synthetic verifier\n", encoding="utf-8")
    legacy_verifier_path = (
        workspace_root / "var/quality/rediar-m05-acceptance/verify-campaign.py"
    )
    legacy_verifier_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_verifier_path.write_text("synthetic legacy verifier\n", encoding="utf-8")

    replay_bindings = []
    for label, path in (
        ("result_summary", result_summary_path),
        ("diagnostic_verification", diagnostic_path),
        ("call_ledger", call_ledger_path),
        ("run_identity", run_identity_path),
    ):
        if path.exists():
            replay_bindings.append(
                {
                    "label": label,
                    **evidence_record(workspace_root, path),
                }
            )
        else:
            replay_bindings.append(
                {
                    "label": label,
                    "path": path.relative_to(workspace_root).as_posix(),
                    "bytes": 0,
                    "sha256": "0" * 64,
                }
            )

    attestation_path = packet_root / "attestation.json"
    attestation = {
        "claim_scope": hybrid_verifier.CLAIM_SCOPE,
        "corpus_on": {
            "authorized": False,
            "physical_correction_calls": 0,
            "status": corpus_on_status,
        },
        "d3_delta_manifest": evidence_record(
            workspace_root,
            d3_manifest_path,
        ),
        "decision": evidence_record(workspace_root, decision_path),
        "freeze_sha": hybrid_verifier.FREEZE_SHA,
        "full_campaign_status": "NOT_EVALUATED",
        "historical_corpus_off_receipts": historical_corpus_receipts,
        "historical_identity_receipts": identity_receipts,
        "legacy_full_verifier": evidence_record(
            workspace_root,
            legacy_verifier_path,
        ),
        "packet_manifest_path": (packet_root / "packet-manifest.sha256")
        .relative_to(workspace_root)
        .as_posix(),
        "promotion_authorized": False,
        "replacement_replay": {
            "artifact_manifest": {
                "records": hybrid_verifier.REPLAY_MANIFEST_RECORDS,
                **evidence_record(workspace_root, replay_manifest_path),
            },
            "bindings": replay_bindings,
            "fixture": hybrid_verifier.REPLACEMENT_FIXTURE,
            "root": replay_root.relative_to(workspace_root).as_posix(),
        },
        "schema_version": hybrid_verifier.ATTESTATION_SCHEMA_VERSION,
        "supplemental_verifier": evidence_record(
            workspace_root,
            synthetic_verifier_path,
        ),
        "future_cap_packet": evidence_record(
            workspace_root,
            cap_packet_path,
        ),
        "verdict": "PASS_HYBRID_FLAG_OFF",
    }
    write_json(attestation_path, attestation)

    packet_manifest_path = packet_root / "packet-manifest.sha256"
    write_sha_manifest(
        packet_root,
        [
            attestation_path,
            cap_packet_path,
            d3_manifest_path,
            decision_path,
        ],
        packet_manifest_path,
    )

    if mutate_source_after_manifest:
        (workspace_root / d3_paths[0]).write_text(
            "changed after manifest\n",
            encoding="utf-8",
        )

    for replay_path in replay_files:
        replay_path.chmod(0o444)
    replay_manifest_path.chmod(0o400)
    for replay_directory in sorted(
        (path for path in replay_root.rglob("*") if path.is_dir()),
        reverse=True,
    ):
        replay_directory.chmod(0o555)
    replay_root.chmod(0o555)

    for packet_path in packet_root.iterdir():
        packet_path.chmod(0o400 if packet_path == packet_manifest_path else 0o444)
    packet_root.chmod(0o555)

    return workspace_root, attestation_path


def verify_synthetic_packet(
    tmp_path: Path,
    **options: object,
) -> Any:
    """Build and verify one isolated packet variant."""
    workspace_root, attestation_path = build_synthetic_hybrid_workspace(
        tmp_path,
        **options,
    )
    return hybrid_verifier.verify_hybrid_attestation(
        workspace_root,
        attestation_path,
    )


def test_valid_hybrid_packet_passes_only_flag_off_scope(tmp_path: Path) -> None:
    """A valid packet emits the narrow verdict, never a full-campaign pass."""
    result = verify_synthetic_packet(tmp_path)

    assert result.ok is True
    assert result.verdict == "PASS_HYBRID_FLAG_OFF"
    assert result.full_campaign_status == "NOT_EVALUATED"
    assert result.failures == []


@pytest.mark.parametrize(
    ("options", "failure_fragment"),
    [
        ({"malformed_d3_hash": True}, "D3 manifest SHA-256"),
        ({"mutate_source_after_manifest": True}, "D3 file SHA-256 mismatch"),
        ({"missing_replay_proof": True}, "replay binding file is missing"),
        (
            {"physical_calls": 5, "fifth_call_attempted": True},
            "physical correction calls must be exactly 4",
        ),
        ({"corpus_on_status": "COMPLETE"}, "corpus-on status must be NOT_EVALUATED"),
        ({"identity_clean": False}, "candidate-surface: clean"),
    ],
)
def test_hybrid_packet_mutations_fail_closed(
    tmp_path: Path,
    options: dict[str, object],
    failure_fragment: str,
) -> None:
    """Any bound identity, hash, replay, call, or scope mutation rejects."""
    result = verify_synthetic_packet(tmp_path, **options)

    assert result.ok is False
    assert any(failure_fragment in failure for failure in result.failures)
