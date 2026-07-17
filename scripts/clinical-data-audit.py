#!/usr/bin/env python3
"""Run the clinical-data safety audit from explicit reviewer inputs.

Use this CPU-only command before a card or rewrite can affect clinician output.
It locks collision checks to ten manifest cases and writes stable JSON plus a table.
It rejects sealed/implicit corpus requests before any consultation file is opened.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_DIRECTORY.parent
DEFAULT_CLINICAL_KNOWLEDGE_PATH = (
    REPOSITORY_ROOT / "strands_agents/data/clinical_knowledge.json"
)
DEFAULT_MEDICAL_LEXICON_PATH = (
    REPOSITORY_ROOT / "strands_agents/data/medical_lexicon.txt"
)
DEFAULT_MEDICAL_LEXICON_REVIEW_PATH = (
    REPOSITORY_ROOT / "strands_agents/data/medical_lexicon_review.json"
)
DEFAULT_DEVELOPMENT_MANIFEST_PATH = (
    REPOSITORY_ROOT / "tests/fixtures/audio/development-corpus-0.5.0.json"
)
DEVELOPMENT_CORPUS_HELPER_PATH = SCRIPT_DIRECTORY / "development-corpus.py"
SHARED_RULES_PATH = SCRIPT_DIRECTORY / "clinical_data_audit_shared.py"
KNOWLEDGE_RULES_PATH = SCRIPT_DIRECTORY / "clinical_data_audit_knowledge.py"
CORE_RULES_PATH = SCRIPT_DIRECTORY / "clinical_data_audit_core.py"
TEXTGRID_TEXT_PATTERN = re.compile(r'^\s*text = "(.*)"\s*$', re.MULTILINE)


class ClinicalDataAuditInputError(ValueError):
    """Explain why a reviewer audit stopped before safe scoring.
    Use for sealed, implicit, unavailable, malformed, or reused inputs.
    Messages are developer-facing and never include consultation content.
    """


def _load_sibling_audit_module(module_name: str, module_path: Path) -> ModuleType:
    """Load one named audit-rule module without changing the host import path.

    Args:
        module_name: Internal import identity; empty would make sibling imports fail.
        module_path: Checked-in sibling file; absent stops the developer audit.

    Returns:
        Loaded module registered only under its explicit internal identity.

    Raises:
        ClinicalDataAuditInputError: The selected rule file cannot be imported.
    """
    module_specification = importlib.util.spec_from_file_location(
        module_name, module_path
    )
    # A missing loader means the safety rules cannot run before user-visible activation.
    if module_specification is None or module_specification.loader is None:
        raise ClinicalDataAuditInputError(f"audit_rules_unavailable: {module_name}")
    audit_module = importlib.util.module_from_spec(module_specification)
    sys.modules[module_name] = audit_module
    module_specification.loader.exec_module(audit_module)
    return audit_module


_SHARED_AUDIT_RULES = _load_sibling_audit_module(
    "clinical_data_audit_shared", SHARED_RULES_PATH
)
_KNOWLEDGE_AUDIT_RULES = _load_sibling_audit_module(
    "clinical_data_audit_knowledge", KNOWLEDGE_RULES_PATH
)
_CORE_AUDIT_RULES = _load_sibling_audit_module(
    "clinical_data_audit_core", CORE_RULES_PATH
)
audit_clinical_data_documents = _CORE_AUDIT_RULES.audit_clinical_data_documents
format_audit_table = _CORE_AUDIT_RULES.format_audit_table
stable_json_bytes = _CORE_AUDIT_RULES.stable_json_bytes


def _load_development_corpus_helper() -> ModuleType:
    """Load the manifest gate without importing application services.

    Returns:
        Helper module; a missing loader stops before fixture access.

    Raises:
        ClinicalDataAuditInputError: The corpus boundary cannot be enforced.
    """
    helper_specification = importlib.util.spec_from_file_location(
        "ambient_scribe_clinical_data_development_corpus",
        DEVELOPMENT_CORPUS_HELPER_PATH,
    )
    # A missing loader means sealed and development identities cannot be separated safely.
    if helper_specification is None or helper_specification.loader is None:
        raise ClinicalDataAuditInputError("development_corpus_helper: unavailable")
    development_corpus_helper = importlib.util.module_from_spec(helper_specification)
    sys.modules[helper_specification.name] = development_corpus_helper
    helper_specification.loader.exec_module(development_corpus_helper)
    return development_corpus_helper


def load_development_truth_utterances(
    manifest_path: Path,
    workspace_root: Path,
    requested_stems: list[str],
) -> tuple[str, ...]:
    """Read only twenty TextGrids from ten explicit approved consultations.

    Args:
        manifest_path: Frozen development manifest; absent stops without discovery.
        workspace_root: Checkout containing approved files; absent resolves no fixture.
        requested_stems: Ten ordered stems; empty or partial is unsafe for this command.

    Returns:
        Official utterances in manifest/channel order; empty means no intervals existed.

    Raises:
        ClinicalDataAuditInputError: Request is sealed, partial, reordered, or invalid.
    """
    development_corpus_helper = _load_development_corpus_helper()
    # Stem validation runs before the manifest or any consultation file is opened.
    try:
        development_corpus_helper.validate_requested_stems(requested_stems)
    # For example, a developer may accidentally paste a sealed holdout into the command.
    except development_corpus_helper.DevelopmentCorpusError as error:
        raise ClinicalDataAuditInputError(str(error)) from error
    # Collision evidence always covers all ten explicit cases, never an implicit/subset run.
    if tuple(requested_stems) != development_corpus_helper.EXPECTED_STEMS:
        raise ClinicalDataAuditInputError(
            "development_stems: expected ten explicit stems in frozen order"
        )
    # The helper supplies exactly twenty validated paths without discovering local extras.
    try:
        textgrid_paths = development_corpus_helper.development_textgrid_paths(
            manifest_path, workspace_root
        )
    # For example, manifest drift or a missing approved file must stop the audit visibly.
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise ClinicalDataAuditInputError(f"development_manifest: {error}") from error
    official_utterances: list[str] = []
    # Each authorized TextGrid contributes intervals without logging their clinical text.
    for textgrid_path in textgrid_paths:
        textgrid_content = textgrid_path.read_text(encoding="utf-8", errors="replace")
        official_utterances.extend(TEXTGRID_TEXT_PATTERN.findall(textgrid_content))
    return tuple(official_utterances)


def _load_json_object(artifact_path: Path, label: str) -> dict[str, Any]:
    """Load one required JSON object and reject missing or malformed input.

    Args:
        artifact_path: Explicit asset; absent or empty stops the reviewer gate.
        label: Safe input name; empty leaves only generic error context.

    Returns:
        Parsed object; never null or a JSON list.

    Raises:
        ClinicalDataAuditInputError: The file cannot provide a structured asset.
    """
    try:
        parsed_document = json.loads(artifact_path.read_text(encoding="utf-8"))
    # For example, a developer may select a half-written JSON file during review.
    except (OSError, json.JSONDecodeError) as error:
        raise ClinicalDataAuditInputError(f"{label}: {error}") from error
    # Lists and null cannot carry the frozen top-level asset schema.
    if not isinstance(parsed_document, dict):
        raise ClinicalDataAuditInputError(f"{label}: expected JSON object")
    return parsed_document


def _write_new_output(output_path: Path, output_bytes: bytes) -> None:
    """Write one evidence artifact without overwriting a prior audit.

    Args:
        output_path: Unique result path; existing means this run ID was reused.
        output_bytes: Stable JSON/table bytes; empty cannot prove reviewer output.

    Raises:
        ClinicalDataAuditInputError: Output exists or contains no evidence.
    """
    # Reusing a path would erase an earlier failed or successful run.
    if output_path.exists():
        raise ClinicalDataAuditInputError(f"output_exists: {output_path}")
    # Empty bytes cannot show what stopped or passed activation.
    if output_bytes == b"":
        raise ClinicalDataAuditInputError(f"empty_output: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(output_bytes)


def parse_arguments() -> argparse.Namespace:
    """Parse explicit assets, ten stems, and unique reviewer outputs.

    Returns:
        CLI values; stem/output fields are non-empty after argparse validation.
    """
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument(
        "--clinical-knowledge", type=Path, default=DEFAULT_CLINICAL_KNOWLEDGE_PATH
    )
    argument_parser.add_argument(
        "--medical-lexicon", type=Path, default=DEFAULT_MEDICAL_LEXICON_PATH
    )
    argument_parser.add_argument(
        "--medical-lexicon-review",
        type=Path,
        default=DEFAULT_MEDICAL_LEXICON_REVIEW_PATH,
    )
    argument_parser.add_argument(
        "--development-manifest",
        type=Path,
        default=DEFAULT_DEVELOPMENT_MANIFEST_PATH,
    )
    argument_parser.add_argument("--workspace-root", type=Path, default=REPOSITORY_ROOT)
    argument_parser.add_argument("--stem", action="append", required=True)
    argument_parser.add_argument("--context-probe", type=Path, default=None)
    argument_parser.add_argument("--json-output", type=Path, required=True)
    argument_parser.add_argument("--table-output", type=Path, required=True)
    return argument_parser.parse_args()


def main() -> int:
    """Run the CPU audit and write stable machine/readable reviewer views.

    Returns:
        Exit 0 for pass, 1 for findings, or 2 for unsafe/unavailable inputs.
    """
    command_arguments = parse_arguments()
    try:
        official_utterances = load_development_truth_utterances(
            command_arguments.development_manifest,
            command_arguments.workspace_root,
            command_arguments.stem,
        )
        clinical_knowledge_document = _load_json_object(
            command_arguments.clinical_knowledge, "clinical_knowledge"
        )
        medical_lexicon_text = command_arguments.medical_lexicon.read_text(
            encoding="utf-8"
        )
        medical_lexicon_review_document = _load_json_object(
            command_arguments.medical_lexicon_review, "medical_lexicon_review"
        )
        context_probe = (
            _load_json_object(command_arguments.context_probe, "context_probe")
            # No probe means prompt visibility is unmeasured in this asset-only run.
            if command_arguments.context_probe is not None
            else None
        )
        development_corpus_helper = _load_development_corpus_helper()
        audit_report = audit_clinical_data_documents(
            clinical_knowledge_document=clinical_knowledge_document,
            medical_lexicon_text=medical_lexicon_text,
            medical_lexicon_review_document=medical_lexicon_review_document,
            context_visibility_probe=context_probe,
            sealed_stems=frozenset(development_corpus_helper.SEALED_STEMS),
            development_truth_utterances=official_utterances,
        )
        _write_new_output(
            command_arguments.json_output, stable_json_bytes(audit_report)
        )
        _write_new_output(
            command_arguments.table_output,
            format_audit_table(audit_report).encode("utf-8"),
        )
    # For example, a sealed stem or reused evidence path stops before a false report appears.
    except (ClinicalDataAuditInputError, OSError, UnicodeError) as error:
        print(f"clinical-data audit stopped: {error}", file=sys.stderr)
        return 2
    print(
        "clinical-data audit "
        f"status={audit_report['status']} "
        f"findings={audit_report['summary']['finding_count']} "
        f"development_utterances={audit_report['summary']['development_truth_utterance_count']}"
    )
    return 0 if audit_report["status"] == "pass" else 1


# Direct execution gives reviewers both stable formats from explicit safe inputs.
if __name__ == "__main__":
    raise SystemExit(main())
