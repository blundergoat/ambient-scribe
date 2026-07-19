"""Verify the CPU audit that protects clinician-visible cards and rewrites.

Use these mock-only tests while changing the clinical-data quality gate.
Each red case names why content stays out of prompts or transcript corrections.
The suite also proves stable outputs and bounded development-manifest access.
No provider, application server, NeMo model, or holdout content is used.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
CLINICAL_DATA_AUDITOR_PATH = REPO_ROOT / "scripts" / "clinical-data-audit.py"
RED_SPECIMEN_PATH = (
    REPO_ROOT / "tests" / "fixtures" / "scribe" / "clinical-data-audit-red-v1.json"
)
RED_SPECIMEN_DOCUMENT = json.loads(RED_SPECIMEN_PATH.read_text(encoding="utf-8"))
CLINICAL_DATA_RED_SPECIMENS = tuple(RED_SPECIMEN_DOCUMENT["specimens"])

# Each unsafe user-facing outcome becomes a readable pytest case name.
CLINICAL_DATA_RED_SPECIMEN_IDS = tuple(
    str(audit_specimen["id"]) for audit_specimen in CLINICAL_DATA_RED_SPECIMENS
)


def write_mock_development_manifest(
    workspace_root: Path, development_stems: tuple[str, ...]
) -> Path:
    """Create a ten-case manifest whose tiny files prove bounded CLI access.
    Use in tests so a reviewer can verify the command without clinical content.

    Args:
        workspace_root: Temporary checkout root; absent cannot hold approved paths.
        development_stems: Frozen ordered names; empty would create an unsafe corpus.

    Returns:
        Manifest path containing exact hashes; it is never null or empty.
    """
    scorer_content = b'"""Synthetic transcript scorer for audit tests."""\n'
    scorer_path = workspace_root / "scripts" / "transcript-quality.py"
    scorer_path.parent.mkdir(parents=True)
    scorer_path.write_bytes(scorer_content)

    fixture_root = workspace_root / "tests" / "fixtures" / "audio"
    fixture_root.mkdir(parents=True)
    manifest_fixtures: list[dict[str, Any]] = []
    # Each visible mock consultation gets audio plus both speaker truth files.
    for fixture_ordinal, development_stem in enumerate(development_stems, start=1):
        fixture_file_records: dict[str, dict[str, Any]] = {}
        fixture_content_by_kind = {
            "wav": b"mock development audio\n",
            "doctor_textgrid": b'text = "Safe doctor development words."\n',
            "patient_textgrid": b'text = "Safe patient development words."\n',
        }
        fixture_suffix_by_kind = {
            "wav": ".wav",
            "doctor_textgrid": ".doctor.TextGrid",
            "patient_textgrid": ".patient.TextGrid",
        }
        # Every manifest record binds the exact bytes the bounded reader may open.
        for fixture_file_kind, fixture_content in fixture_content_by_kind.items():
            relative_fixture_path = Path(
                f"tests/fixtures/audio/{development_stem}"
                f"{fixture_suffix_by_kind[fixture_file_kind]}"
            )
            fixture_path = workspace_root / relative_fixture_path
            fixture_path.write_bytes(fixture_content)
            fixture_file_records[fixture_file_kind] = {
                "path": relative_fixture_path.as_posix(),
                "bytes": len(fixture_content),
                "sha256": hashlib.sha256(fixture_content).hexdigest(),
            }
        manifest_fixtures.append(
            {
                "ordinal": fixture_ordinal,
                "fixture_id": development_stem,
                "stem": development_stem,
                "files": fixture_file_records,
            }
        )
    manifest_path = workspace_root / "development-corpus.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "ambient-scribe-development-corpus/v1",
                "scorer": {
                    "version": "synthetic-test-scorer/v1",
                    "path": "scripts/transcript-quality.py",
                    "bytes": len(scorer_content),
                    "sha256": hashlib.sha256(scorer_content).hexdigest(),
                },
                "fixtures": manifest_fixtures,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return manifest_path


def write_mock_clinical_assets(workspace_root: Path) -> dict[str, Path]:
    """Write the valid mock cards and rewrite ledger for a CLI audit.
    Use to exercise reviewer outputs without inspecting production clinical data.

    Args:
        workspace_root: Temporary evidence root; absent cannot hold command inputs.

    Returns:
        Named asset paths; none are null or empty after successful writes.
    """
    mock_assets = copy.deepcopy(RED_SPECIMEN_DOCUMENT["base_assets"])
    clinical_knowledge_path = workspace_root / "clinical-knowledge.json"
    medical_lexicon_path = workspace_root / "medical-lexicon.txt"
    medical_lexicon_review_path = workspace_root / "medical-lexicon-review.json"
    clinical_knowledge_path.write_text(
        json.dumps(mock_assets["clinical_knowledge"], sort_keys=True), encoding="utf-8"
    )
    medical_lexicon_path.write_text(
        mock_assets["medical_lexicon_text"], encoding="utf-8"
    )
    medical_lexicon_review_path.write_text(
        json.dumps(mock_assets["medical_lexicon_review"], sort_keys=True),
        encoding="utf-8",
    )
    return {
        "clinical_knowledge": clinical_knowledge_path,
        "medical_lexicon": medical_lexicon_path,
        "medical_lexicon_review": medical_lexicon_review_path,
    }


def load_clinical_data_auditor() -> ModuleType:
    """Load the CPU-only auditor a developer runs before activating clinical data.
    A missing or unimportable script keeps every unsafe specimen visibly red.

    Returns:
        Imported auditor module; it is never null on a usable quality run.
    """
    # No script means the developer has no gate preventing unsafe cards or rewrites.
    if not CLINICAL_DATA_AUDITOR_PATH.is_file():
        pytest.fail(
            f"required clinical-data auditor is missing: {CLINICAL_DATA_AUDITOR_PATH}"
        )

    auditor_specification = importlib.util.spec_from_file_location(
        "ambient_scribe_clinical_data_audit",
        CLINICAL_DATA_AUDITOR_PATH,
    )
    # No import loader means the quality gate cannot inspect assets before users see them.
    if auditor_specification is None or auditor_specification.loader is None:
        pytest.fail(
            f"clinical-data auditor is not importable: {CLINICAL_DATA_AUDITOR_PATH}"
        )

    clinical_data_auditor = importlib.util.module_from_spec(auditor_specification)
    sys.modules[auditor_specification.name] = clinical_data_auditor
    auditor_specification.loader.exec_module(clinical_data_auditor)
    return clinical_data_auditor


def test_red_fixture_freezes_every_named_clinical_data_failure() -> None:
    """The developer sees all eight contract failures before implementing the gate.
    Use this to catch a dropped or silently renamed safety case in the mock fixture.
    """
    assert (
        RED_SPECIMEN_DOCUMENT["schema_version"]
        == "ambient-scribe-clinical-data-red-specimens/v1"
    )
    assert (
        RED_SPECIMEN_DOCUMENT["asset_contract_version"]
        == "ambient-scribe-clinical-data-contract/v1"
    )
    assert 3 <= len(RED_SPECIMEN_DOCUMENT["description_lines"]) <= 8
    assert RED_SPECIMEN_DOCUMENT["required_finding_ids"] == [
        "review.missing_active_pair",
        "review.generic_active_provenance",
        "review.duplicate_pair",
        "lexicon.variant_collision",
        "review.active_semantic_synonym",
        "knowledge.keyword_substring_collision",
        "knowledge.inactive_context_injected",
        "privacy.sealed_holdout_source",
    ]
    assert len(CLINICAL_DATA_RED_SPECIMENS) == 8

    # Finding order is frozen so later audit output cannot hide a dropped safety case.
    assert [
        audit_specimen["expected_finding"]["finding_id"]
        for audit_specimen in CLINICAL_DATA_RED_SPECIMENS
    ] == RED_SPECIMEN_DOCUMENT["required_finding_ids"]

    # Every mock ledger binds the exact executable TXT bytes used by its red case.
    for audit_specimen in CLINICAL_DATA_RED_SPECIMENS:
        # Empty overrides mean the valid mock assets remain selected for this case.
        selected_mock_assets = {
            **RED_SPECIMEN_DOCUMENT["base_assets"],
            **audit_specimen.get("asset_overrides", {}),
        }
        selected_lexicon_bytes = selected_mock_assets["medical_lexicon_text"].encode(
            "utf-8"
        )
        assert (
            hashlib.sha256(selected_lexicon_bytes).hexdigest()
            == (
                selected_mock_assets["medical_lexicon_review"]["runtime_lexicon"][
                    "sha256"
                ]
            )
        )

    # The privacy case carries a plan-listed name and mock metadata, never clinical content.
    sealed_holdout_specimen = CLINICAL_DATA_RED_SPECIMENS[-1]
    sealed_source_artifact = sealed_holdout_specimen["asset_overrides"][
        "medical_lexicon_review"
    ]["entries"][0]["source_artifact"]
    assert (
        sealed_source_artifact["artifact_id"] in RED_SPECIMEN_DOCUMENT["sealed_stems"]
    )
    assert sealed_source_artifact["span"] == "hash-only-name-no-content"
    assert set(sealed_source_artifact) == {
        "artifact_id",
        "sha256",
        "span",
        "corpus_class",
    }


def test_valid_clinical_data_outputs_are_byte_stable() -> None:
    """A safe mock produces repeatable JSON and the same reviewer table.
    Use before trusting audit evidence or showing a pass to a developer.
    """
    clinical_data_auditor = load_clinical_data_auditor()
    valid_mock_assets = copy.deepcopy(RED_SPECIMEN_DOCUMENT["base_assets"])
    audit_inputs = {
        "clinical_knowledge_document": valid_mock_assets["clinical_knowledge"],
        "medical_lexicon_text": valid_mock_assets["medical_lexicon_text"],
        "medical_lexicon_review_document": valid_mock_assets["medical_lexicon_review"],
        "context_visibility_probe": None,
        "sealed_stems": frozenset(RED_SPECIMEN_DOCUMENT["sealed_stems"]),
    }

    first_safety_report = clinical_data_auditor.audit_clinical_data_documents(
        **audit_inputs
    )
    second_safety_report = clinical_data_auditor.audit_clinical_data_documents(
        **audit_inputs
    )
    first_json_bytes = clinical_data_auditor.stable_json_bytes(first_safety_report)
    second_json_bytes = clinical_data_auditor.stable_json_bytes(second_safety_report)
    first_reviewer_table = clinical_data_auditor.format_audit_table(first_safety_report)
    second_reviewer_table = clinical_data_auditor.format_audit_table(
        second_safety_report
    )

    assert first_safety_report["status"] == "pass"
    assert first_safety_report["findings"] == []
    assert first_json_bytes == second_json_bytes
    assert first_reviewer_table == second_reviewer_table
    assert first_reviewer_table.endswith("\n")


def test_import_stays_outside_application_and_gpu_services() -> None:
    """Importing the auditor loads no FastAPI, torch, or NeMo module.
    Use to prove a reviewer can run the gate without starting runtime services.
    """
    import_probe = f"""
import importlib.util
import json
import sys
from pathlib import Path
auditor_path = Path({str(CLINICAL_DATA_AUDITOR_PATH)!r})
specification = importlib.util.spec_from_file_location('audit_import_probe', auditor_path)
module = importlib.util.module_from_spec(specification)
sys.modules[specification.name] = module
specification.loader.exec_module(module)
forbidden = ('fastapi', 'nemo', 'torch')
print(json.dumps(sorted(name for name in sys.modules if name.split('.')[0] in forbidden)))
"""
    import_result = subprocess.run(
        [
            sys.executable,
            "-c",
            import_probe,
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert import_result.returncode == 0, import_result.stderr
    assert import_result.stdout == "[]\n"


def test_sealed_stem_is_rejected_before_manifest_or_truth_access(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A sealed name stops before any manifest or consultation text is read.
    Use when checking the privacy boundary on a developer-selected corpus.

    Args:
        monkeypatch: Test seam; absent cannot prove that reads stayed at zero.
        tmp_path: Unreadable sentinel location; empty still remains outside the repo.
    """
    clinical_data_auditor = load_clinical_data_auditor()
    development_corpus_helper = clinical_data_auditor._load_development_corpus_helper()
    sealed_stem = sorted(development_corpus_helper.SEALED_STEMS)[0]
    opened_paths: list[Path] = []

    def record_forbidden_read(selected_path: Path, *args: Any, **kwargs: Any) -> str:
        """Record a forbidden file read so the privacy assertion fails clearly.

        Args:
            selected_path: Candidate file; absent cannot identify a boundary breach.
            args: Reader options; empty means pathlib defaults would have applied.
            kwargs: Named reader options; empty means pathlib defaults would have applied.

        Returns:
            No content; this function always raises after recording the path.
        """
        opened_paths.append(selected_path)
        raise AssertionError(f"sealed request opened {selected_path}")

    monkeypatch.setattr(
        clinical_data_auditor,
        "_load_development_corpus_helper",
        lambda: development_corpus_helper,
    )
    monkeypatch.setattr(Path, "read_text", record_forbidden_read)

    with pytest.raises(
        clinical_data_auditor.ClinicalDataAuditInputError, match="sealed_stem"
    ):
        clinical_data_auditor.load_development_truth_utterances(
            tmp_path / "must-not-open.json",
            tmp_path,
            [sealed_stem],
        )

    assert opened_paths == []


def test_collision_reader_uses_twenty_manifest_textgrids_without_discovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ten explicit cases open only their twenty speaker truth files for text.
    Use to catch accidental globs, implicit corpus selection, or local extras.

    Args:
        monkeypatch: Test seam; absent cannot detect discovery calls.
        tmp_path: Isolated corpus root; empty still contains no clinical data.
    """
    clinical_data_auditor = load_clinical_data_auditor()
    development_corpus_helper = clinical_data_auditor._load_development_corpus_helper()
    development_stems = tuple(development_corpus_helper.EXPECTED_STEMS)
    manifest_path = write_mock_development_manifest(tmp_path, development_stems)
    original_read_text = Path.read_text
    opened_text_paths: list[Path] = []

    def record_approved_text_read(
        selected_path: Path, *args: Any, **kwargs: Any
    ) -> str:
        """Record manifest/truth reads while returning their safe mock content.

        Args:
            selected_path: Approved mock file; absent cannot be opened.
            args: Reader options; empty means pathlib defaults apply.
            kwargs: Named reader options; empty means pathlib defaults apply.

        Returns:
            Mock file content; it is empty only if the fixture was written empty.
        """
        opened_text_paths.append(selected_path)
        return original_read_text(selected_path, *args, **kwargs)

    def reject_path_discovery(*args: Any, **kwargs: Any) -> None:
        """Fail if the collision reader tries to discover unlisted local files.

        Args:
            args: Discovery values; empty still means a forbidden implicit scan.
            kwargs: Discovery options; empty still means a forbidden implicit scan.
        """
        raise AssertionError("collision reader attempted path discovery")

    monkeypatch.setattr(Path, "read_text", record_approved_text_read)
    monkeypatch.setattr(Path, "glob", reject_path_discovery)
    monkeypatch.setattr(Path, "rglob", reject_path_discovery)
    official_utterances = clinical_data_auditor.load_development_truth_utterances(
        manifest_path,
        tmp_path,
        list(development_stems),
    )

    opened_truth_paths = [
        opened_path
        for opened_path in opened_text_paths
        if opened_path.suffix == ".TextGrid"
    ]
    assert len(development_stems) == 10
    assert len(opened_truth_paths) == 20
    assert len(set(opened_truth_paths)) == 20
    assert len(official_utterances) == 20
    assert opened_text_paths[0] == manifest_path


def test_cli_writes_repeatable_outputs_and_refuses_overwrite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The command writes stable reviewer evidence once and preserves prior runs.
    Use when a developer needs comparable JSON/table artifacts from safe inputs.

    Args:
        monkeypatch: CLI seam; absent cannot provide the explicit test arguments.
        tmp_path: Unique run root; empty still cannot overwrite repository evidence.
        capsys: Captured status text; absent cannot prove the visible stop reason.
    """
    clinical_data_auditor = load_clinical_data_auditor()
    development_corpus_helper = clinical_data_auditor._load_development_corpus_helper()
    development_stems = tuple(development_corpus_helper.EXPECTED_STEMS)
    manifest_path = write_mock_development_manifest(tmp_path, development_stems)
    clinical_asset_paths = write_mock_clinical_assets(tmp_path)
    first_json_path = tmp_path / "run-1.json"
    first_table_path = tmp_path / "run-1.txt"
    second_json_path = tmp_path / "run-2.json"
    second_table_path = tmp_path / "run-2.txt"

    def command_arguments(json_path: Path, table_path: Path) -> list[str]:
        """Build one exact ten-case command for unique reviewer outputs.

        Args:
            json_path: Machine report target; an existing path must be rejected.
            table_path: Reviewer table target; an existing path must be rejected.

        Returns:
            Complete CLI values; the list is never empty.
        """
        explicit_stem_arguments = [
            argument_value
            # Each explicit stem remains paired with its own CLI option.
            for development_stem in development_stems
            for argument_value in ("--stem", development_stem)
        ]
        return [
            str(CLINICAL_DATA_AUDITOR_PATH),
            "--clinical-knowledge",
            str(clinical_asset_paths["clinical_knowledge"]),
            "--medical-lexicon",
            str(clinical_asset_paths["medical_lexicon"]),
            "--medical-lexicon-review",
            str(clinical_asset_paths["medical_lexicon_review"]),
            "--development-manifest",
            str(manifest_path),
            "--workspace-root",
            str(tmp_path),
            *explicit_stem_arguments,
            "--json-output",
            str(json_path),
            "--table-output",
            str(table_path),
        ]

    monkeypatch.setattr(
        sys, "argv", command_arguments(first_json_path, first_table_path)
    )
    first_exit_code = clinical_data_auditor.main()
    first_status_output = capsys.readouterr()
    monkeypatch.setattr(
        sys, "argv", command_arguments(second_json_path, second_table_path)
    )
    second_exit_code = clinical_data_auditor.main()
    second_status_output = capsys.readouterr()

    assert first_exit_code == 0
    assert second_exit_code == 0
    assert first_status_output.out == second_status_output.out
    assert first_json_path.read_bytes() == second_json_path.read_bytes()
    assert first_table_path.read_bytes() == second_table_path.read_bytes()

    monkeypatch.setattr(
        sys, "argv", command_arguments(first_json_path, first_table_path)
    )
    overwrite_exit_code = clinical_data_auditor.main()
    overwrite_status_output = capsys.readouterr()

    assert overwrite_exit_code == 2
    assert "output_exists" in overwrite_status_output.err


@pytest.mark.parametrize(
    "audit_specimen",
    CLINICAL_DATA_RED_SPECIMENS,
    ids=CLINICAL_DATA_RED_SPECIMEN_IDS,
)
def test_unsafe_clinical_data_stays_out_of_clinician_visible_output(
    audit_specimen: dict[str, Any],
) -> None:
    """Each unsafe card or term produces its one frozen fail-closed finding.
    Use while implementing the auditor so one green aggregate cannot hide a bad pair.

    Args:
        audit_specimen: One named mock failure; empty means no safety outcome can be verified.
    """
    clinical_data_auditor = load_clinical_data_auditor()

    base_clinical_data_assets = copy.deepcopy(RED_SPECIMEN_DOCUMENT["base_assets"])
    # Empty overrides mean this case uses every valid mock asset unchanged.
    specimen_asset_overrides = copy.deepcopy(audit_specimen.get("asset_overrides", {}))
    selected_clinical_data_assets = {
        **base_clinical_data_assets,
        **specimen_asset_overrides,
    }
    # An absent probe means this case tests only asset validation, not prompt injection.
    context_visibility_probe = audit_specimen.get("context_probe")

    clinician_data_safety_report = clinical_data_auditor.audit_clinical_data_documents(
        clinical_knowledge_document=selected_clinical_data_assets["clinical_knowledge"],
        medical_lexicon_text=selected_clinical_data_assets["medical_lexicon_text"],
        medical_lexicon_review_document=selected_clinical_data_assets[
            "medical_lexicon_review"
        ],
        context_visibility_probe=context_visibility_probe,
        sealed_stems=frozenset(RED_SPECIMEN_DOCUMENT["sealed_stems"]),
    )

    # Every mock changes one dimension so the UI-facing stop reason stays unambiguous.
    clinician_visible_finding_summaries = [
        {
            "finding_id": safety_finding.get("finding_id"),
            "asset": safety_finding.get("asset"),
            "entry_id": safety_finding.get("entry_id"),
        }
        for safety_finding in clinician_data_safety_report["findings"]
    ]
    assert clinician_data_safety_report["schema_version"] == (
        "ambient-scribe-clinical-data-audit/v1"
    )
    assert clinician_data_safety_report["asset_contract_version"] == (
        "ambient-scribe-clinical-data-contract/v1"
    )
    assert clinician_data_safety_report["status"] == "fail"
    assert clinician_visible_finding_summaries == [audit_specimen["expected_finding"]]
