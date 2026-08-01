"""Tests for the M05 evaluator/manifest composition verifier."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFIER_PATH = REPO_ROOT / "scripts/verify-evaluator-contract.py"


def load_contract_verifier() -> ModuleType:
    """Load the exact CPU-only verifier file used by the recovery runner."""
    verifier_spec = importlib.util.spec_from_file_location(
        "ambient_scribe_m05_evaluator_contract_test_module",
        VERIFIER_PATH,
    )
    assert verifier_spec is not None
    assert verifier_spec.loader is not None

    verifier = importlib.util.module_from_spec(verifier_spec)
    sys.modules[verifier_spec.name] = verifier
    verifier_spec.loader.exec_module(verifier)
    return verifier


def file_identity(path: Path) -> dict[str, object]:
    """Return the byte/hash record frozen by a synthetic contract."""
    content = path.read_bytes()
    return {
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def write_contract_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, Path, dict[str, object]]:
    """Write a valid two-evaluator contract and its bound manifest."""
    scripts_root = tmp_path / "scripts"
    fixtures_root = tmp_path / "tests" / "fixtures"
    scripts_root.mkdir(parents=True)
    fixtures_root.mkdir(parents=True)

    primary_scorer = scripts_root / "transcript-quality.py"
    primary_scorer.write_text('"""Primary scorer."""\n', encoding="utf-8")
    clinical_evaluator = scripts_root / "clinical-term-identity.py"
    clinical_evaluator.write_text('"""Clinical evaluator."""\n', encoding="utf-8")
    verification_file = tmp_path / "tests" / "test_contract.py"
    verification_file.write_text('"""Contract test."""\n', encoding="utf-8")
    primary_identity = file_identity(primary_scorer)

    manifest = fixtures_root / "development-corpus.json"
    manifest.write_text(
        json.dumps(
            {
                "scorer": {
                    "path": "scripts/transcript-quality.py",
                    **primary_identity,
                }
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    contract_document: dict[str, object] = {
        "schema_version": "ambient-scribe-m05-evaluator-contract/v1",
        "evaluators": {
            "scripts/transcript-quality.py": primary_identity,
            "scripts/clinical-term-identity.py": file_identity(clinical_evaluator),
        },
        "verification_files": {
            "tests/test_contract.py": file_identity(verification_file),
        },
        "scorer_manifest_pairs": [
            {
                "manifest_path": "tests/fixtures/development-corpus.json",
                "manifest_identity": file_identity(manifest),
                "scorer_path": "scripts/transcript-quality.py",
            }
        ],
    }
    contract = tmp_path / "m05-contract.json"
    contract.write_text(
        json.dumps(contract_document, sort_keys=True),
        encoding="utf-8",
    )
    return contract, manifest, primary_scorer, contract_document


def test_composed_contract_verifies_all_evaluators_and_manifest_pairs(
    tmp_path: Path,
) -> None:
    """The receipt counts every frozen evaluator and bound scorer/manifest pair."""
    verifier = load_contract_verifier()
    contract, _manifest, _primary_scorer, _document = write_contract_fixture(tmp_path)

    result = verifier.verify_m05_evaluator_contract(contract, tmp_path)

    assert result == {
        "schema_version": "ambient-scribe-m05-evaluator-contract/v1",
        "status": "valid",
        "evaluator_count": 2,
        "verification_file_count": 1,
        "scorer_manifest_pair_count": 1,
        "scorer_manifest_pairs": [
            {
                "manifest_path": "tests/fixtures/development-corpus.json",
                "scorer_path": "scripts/transcript-quality.py",
            }
        ],
    }


def test_individually_valid_files_cannot_freeze_a_mismatched_scorer_pair(
    tmp_path: Path,
) -> None:
    """A manifest and evaluator with valid own hashes still must agree with each other."""
    verifier = load_contract_verifier()
    contract, manifest, _primary_scorer, contract_document = write_contract_fixture(
        tmp_path
    )
    manifest_document = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_document["scorer"]["sha256"] = "0" * 64
    manifest.write_text(
        json.dumps(manifest_document, sort_keys=True),
        encoding="utf-8",
    )
    contract_document["scorer_manifest_pairs"][0]["manifest_identity"] = file_identity(
        manifest
    )
    contract.write_text(
        json.dumps(contract_document, sort_keys=True),
        encoding="utf-8",
    )

    with pytest.raises(
        verifier.M05EvaluatorContractError,
        match="manifest scorer sha256 disagrees",
    ):
        verifier.verify_m05_evaluator_contract(contract, tmp_path)


def test_evaluator_hash_drift_fails_before_pair_acceptance(tmp_path: Path) -> None:
    """Changed evaluator bytes cannot be hidden behind a still-valid manifest."""
    verifier = load_contract_verifier()
    contract, _manifest, primary_scorer, _document = write_contract_fixture(tmp_path)
    primary_scorer.write_text('"""Changed scorer."""\n', encoding="utf-8")

    with pytest.raises(
        verifier.M05EvaluatorContractError,
        match="evaluator_(size|hash)_drift",
    ):
        verifier.verify_m05_evaluator_contract(contract, tmp_path)


def test_contract_rejects_repository_path_escape(tmp_path: Path) -> None:
    """Evaluator paths cannot read identities outside the declared checkout."""
    verifier = load_contract_verifier()
    contract, _manifest, _primary_scorer, contract_document = write_contract_fixture(
        tmp_path
    )
    evaluator_records = contract_document["evaluators"]
    evaluator_records["../outside.py"] = evaluator_records.pop(
        "scripts/clinical-term-identity.py"
    )
    contract.write_text(
        json.dumps(contract_document, sort_keys=True),
        encoding="utf-8",
    )

    with pytest.raises(
        verifier.M05EvaluatorContractError,
        match="evaluator_path",
    ):
        verifier.verify_m05_evaluator_contract(contract, tmp_path)
