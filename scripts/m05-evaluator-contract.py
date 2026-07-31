"""Verify every evaluator and scorer/manifest pair frozen by an approval packet.

The verifier is CPU-only and reads repository identities without opening any
development audio or transcript truth. It rejects a packet when its files hash
correctly in isolation but a manifest names different primary-scorer bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "ambient-scribe-m05-evaluator-contract/v1"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class M05EvaluatorContractError(ValueError):
    """Raised when a frozen evaluator contract cannot compose safely."""


def _reject(category: str, detail: str) -> M05EvaluatorContractError:
    """Return one stable fail-closed contract error."""
    return M05EvaluatorContractError(f"{category}: {detail}")


def _file_sha256(path: Path) -> str:
    """Return the SHA-256 of one frozen contract input."""
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for block in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _workspace_file(
    workspace_root: Path,
    relative_path_value: object,
    *,
    category: str,
) -> tuple[str, Path]:
    """Resolve one repository-relative file without allowing path escape."""
    if not isinstance(relative_path_value, str) or relative_path_value == "":
        raise _reject(category, "path must be a non-empty string")

    relative_path = Path(relative_path_value)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise _reject(category, relative_path_value)

    resolved_workspace_root = workspace_root.resolve()
    resolved_path = (resolved_workspace_root / relative_path).resolve()
    if not resolved_path.is_relative_to(resolved_workspace_root):
        raise _reject(category, relative_path_value)
    if not resolved_path.is_file():
        raise _reject(category, relative_path_value)
    return relative_path.as_posix(), resolved_path


def _identity_record(
    record: object,
    *,
    category: str,
) -> tuple[int, str]:
    """Return a validated byte count and SHA-256 identity."""
    if not isinstance(record, dict):
        raise _reject(category, "identity must be an object")

    expected_bytes = record.get("bytes")
    expected_sha256 = record.get("sha256")
    if not isinstance(expected_bytes, int) or expected_bytes < 0:
        raise _reject(category, "bytes must be a non-negative integer")
    if (
        not isinstance(expected_sha256, str)
        or SHA256_PATTERN.fullmatch(expected_sha256) is None
    ):
        raise _reject(category, "sha256 must be 64 lowercase hexadecimal characters")
    return expected_bytes, expected_sha256


def _verify_file_identity(
    path: Path,
    identity_record: object,
    *,
    category: str,
) -> tuple[int, str]:
    """Verify one file's size and SHA-256 against its contract record."""
    expected_bytes, expected_sha256 = _identity_record(
        identity_record,
        category=category,
    )
    if path.stat().st_size != expected_bytes:
        raise _reject(f"{category}_size_drift", path.as_posix())
    if _file_sha256(path) != expected_sha256:
        raise _reject(f"{category}_hash_drift", path.as_posix())
    return expected_bytes, expected_sha256


def _path_identity_records(
    contract_document: dict[str, Any],
    field_name: str,
) -> dict[str, Any]:
    """Return one non-empty path-keyed identity map from the packet."""
    identity_records = contract_document.get(field_name)
    if not isinstance(identity_records, dict) or not identity_records:
        raise _reject(field_name, "must be a non-empty object")
    if any(not isinstance(path, str) or path == "" for path in identity_records):
        raise _reject(field_name, "every key must be a non-empty path")
    return identity_records


def verify_m05_evaluator_contract(
    contract_path: Path,
    workspace_root: Path,
) -> dict[str, Any]:
    """Verify evaluator identities and every declared scorer/manifest pair.

    Args:
        contract_path: Packet-owned JSON contract.
        workspace_root: Checkout containing every declared repository-relative path.

    Returns:
        Text-free verification summary suitable for a CPU preflight receipt.

    Raises:
        M05EvaluatorContractError: The schema, file identity, or pair composition
            is invalid.
        OSError: A required contract file cannot be read.
        json.JSONDecodeError: A declared JSON document cannot be parsed.
    """
    contract_document = json.loads(contract_path.read_text(encoding="utf-8"))
    if not isinstance(contract_document, dict):
        raise _reject("schema", "contract must be an object")
    if contract_document.get("schema_version") != SCHEMA_VERSION:
        raise _reject("schema", str(contract_document.get("schema_version")))

    evaluator_records = _path_identity_records(contract_document, "evaluators")
    verified_evaluators: dict[str, tuple[int, str]] = {}
    for evaluator_path_value, evaluator_record in evaluator_records.items():
        evaluator_path, resolved_evaluator_path = _workspace_file(
            workspace_root,
            evaluator_path_value,
            category="evaluator_path",
        )
        verified_evaluators[evaluator_path] = _verify_file_identity(
            resolved_evaluator_path,
            evaluator_record,
            category="evaluator",
        )

    verification_file_records = _path_identity_records(
        contract_document,
        "verification_files",
    )
    verified_verification_files: list[str] = []
    for verification_path_value, verification_record in (
        verification_file_records.items()
    ):
        verification_path, resolved_verification_path = _workspace_file(
            workspace_root,
            verification_path_value,
            category="verification_file_path",
        )
        _verify_file_identity(
            resolved_verification_path,
            verification_record,
            category="verification_file",
        )
        verified_verification_files.append(verification_path)

    pair_records = contract_document.get("scorer_manifest_pairs")
    if not isinstance(pair_records, list) or not pair_records:
        raise _reject("scorer_manifest_pairs", "must be a non-empty array")

    verified_pairs: list[dict[str, str]] = []
    for pair_index, pair_record in enumerate(pair_records):
        pair_category = f"scorer_manifest_pair_{pair_index}"
        if not isinstance(pair_record, dict):
            raise _reject(pair_category, "pair must be an object")

        manifest_path, resolved_manifest_path = _workspace_file(
            workspace_root,
            pair_record.get("manifest_path"),
            category=f"{pair_category}_manifest_path",
        )
        _verify_file_identity(
            resolved_manifest_path,
            pair_record.get("manifest_identity"),
            category=f"{pair_category}_manifest",
        )
        scorer_path_value = pair_record.get("scorer_path")
        if not isinstance(scorer_path_value, str):
            raise _reject(pair_category, "scorer_path must be a string")
        scorer_path = Path(scorer_path_value).as_posix()
        if scorer_path not in verified_evaluators:
            raise _reject(pair_category, f"unregistered scorer {scorer_path}")

        manifest_document = json.loads(
            resolved_manifest_path.read_text(encoding="utf-8")
        )
        if not isinstance(manifest_document, dict):
            raise _reject(pair_category, "manifest must be an object")
        manifest_scorer = manifest_document.get("scorer")
        if not isinstance(manifest_scorer, dict):
            raise _reject(pair_category, "manifest scorer must be an object")

        evaluator_bytes, evaluator_sha256 = verified_evaluators[scorer_path]
        if manifest_scorer.get("path") != scorer_path:
            raise _reject(pair_category, "manifest scorer path disagrees")
        if manifest_scorer.get("bytes") != evaluator_bytes:
            raise _reject(pair_category, "manifest scorer bytes disagree")
        if manifest_scorer.get("sha256") != evaluator_sha256:
            raise _reject(pair_category, "manifest scorer sha256 disagrees")

        verified_pairs.append(
            {
                "manifest_path": manifest_path,
                "scorer_path": scorer_path,
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "valid",
        "evaluator_count": len(verified_evaluators),
        "verification_file_count": len(verified_verification_files),
        "scorer_manifest_pair_count": len(verified_pairs),
        "scorer_manifest_pairs": verified_pairs,
    }


def parse_args() -> argparse.Namespace:
    """Parse the contract path and checkout root."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("contract", type=Path)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    return parser.parse_args()


def main() -> int:
    """Verify one packet contract and emit a deterministic JSON receipt."""
    arguments = parse_args()
    try:
        result = verify_m05_evaluator_contract(
            arguments.contract,
            arguments.repo_root,
        )
    except (M05EvaluatorContractError, OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"evaluator contract rejected: {error}") from error

    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
