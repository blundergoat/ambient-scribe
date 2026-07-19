"""Protect the developer's 0.5.0 consultation picker and evaluation inputs.

Use these CPU-only tests before a baseline or vocabulary sweep can open local
fixtures. They prove the helper exposes exactly ten approved consultations and
rejects a sealed choice without reading its content.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER_PATH = REPO_ROOT / "scripts" / "development-corpus.py"
DEMO_AUDIO_GENERATOR_PATH = REPO_ROOT / "scripts/generate-demo-consultation-audio.py"
PRIMOCK57_TRANSCRIPT_DOWNLOADER_PATH = (
    REPO_ROOT / "scripts/download-primock57-transcripts.sh"
)
MANIFEST_PATH = (
    REPO_ROOT / "tests" / "fixtures" / "audio" / "development-corpus-0.5.0.json"
)
PICKER_CATALOG_PATH = (
    REPO_ROOT / "tests" / "fixtures" / "audio" / "generated-manifest.json"
)
SEALED_SPECIMEN = "primock57-day2-consultation07-im-having-chest-discomfort"
EXPECTED_DEVELOPMENT_CASE_IDS = (
    "day1_consultation02",
    "day1_consultation03",
    "day1_consultation06",
    "day1_consultation07",
    "day1_consultation08",
    "day2_consultation03",
    "day2_consultation09",
    "day3_consultation01",
    "day5_consultation03",
    "day5_consultation09",
)
RUN_PROVISIONED_QUALITY_TESTS = (
    os.environ.get("AMBIENT_SCRIBE_RUN_PROVISIONED_QUALITY_TESTS") == "1"
)


def write_synthetic_development_manifest(
    workspace_root: Path, development_corpus_helper: ModuleType
) -> Path:
    """Write hashed mock scorer and triplets for default corpus unit coverage."""
    scorer_content = b'"""Synthetic transcript scorer for corpus tests."""\n'
    scorer_path = workspace_root / "scripts" / "transcript-quality.py"
    scorer_path.parent.mkdir(parents=True)
    scorer_path.write_bytes(scorer_content)

    fixture_root = workspace_root / "tests" / "fixtures" / "audio"
    fixture_root.mkdir(parents=True)
    manifest_fixtures = []
    for fixture_ordinal, development_stem in enumerate(
        development_corpus_helper.EXPECTED_STEMS, start=1
    ):
        fixture_file_records = {}
        fixture_content_by_kind = {
            "wav": f"mock audio {fixture_ordinal}\n".encode(),
            "doctor_textgrid": (
                f'text = "Safe doctor words {fixture_ordinal}."\n'.encode()
            ),
            "patient_textgrid": (
                f'text = "Safe patient words {fixture_ordinal}."\n'.encode()
            ),
        }
        fixture_suffix_by_kind = {
            "wav": ".wav",
            "doctor_textgrid": ".doctor.TextGrid",
            "patient_textgrid": ".patient.TextGrid",
        }
        for fixture_file_kind, fixture_content in fixture_content_by_kind.items():
            relative_fixture_path = Path(
                f"tests/fixtures/audio/{development_stem}"
                f"{fixture_suffix_by_kind[fixture_file_kind]}"
            )
            (workspace_root / relative_fixture_path).write_bytes(fixture_content)
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

    manifest_path = fixture_root / "synthetic-development-corpus.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": development_corpus_helper.SCHEMA_VERSION,
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


def load_development_corpus_helper() -> ModuleType:
    """Load the corpus helper as the developer CLI would use it before evaluation.
    The registered module identity keeps its validated fixture records importable.
    """
    helper_spec = importlib.util.spec_from_file_location(
        "ambient_scribe_development_corpus_test",
        HELPER_PATH,
    )

    # No import specification means the developer cannot run the corpus safety gate.
    assert helper_spec is not None
    # No loader means the safety helper cannot validate any selected consultations.
    assert helper_spec.loader is not None

    development_corpus_helper = importlib.util.module_from_spec(helper_spec)
    sys.modules[helper_spec.name] = development_corpus_helper
    helper_spec.loader.exec_module(development_corpus_helper)
    return development_corpus_helper


def load_demo_audio_generator() -> ModuleType:
    """Load picker-generation rules without generating or downloading audio.
    Use when a developer verifies which consultations users can select in Demo Audio.
    """
    generator_spec = importlib.util.spec_from_file_location(
        "ambient_scribe_demo_audio_generator_test",
        DEMO_AUDIO_GENERATOR_PATH,
    )

    # No import specification means the developer cannot verify the picker source safely.
    assert generator_spec is not None
    # No loader means the picker inventory cannot be checked before fixture generation.
    assert generator_spec.loader is not None

    demo_audio_generator = importlib.util.module_from_spec(generator_spec)
    sys.modules[generator_spec.name] = demo_audio_generator
    generator_spec.loader.exec_module(demo_audio_generator)
    return demo_audio_generator


def test_tracked_manifest_resolves_ten_paths_without_opening_local_corpus() -> None:
    """The tracked contract keeps ten ordered cases without requiring ignored bytes."""
    development_corpus_helper = load_development_corpus_helper()

    development_fixtures = development_corpus_helper.load_development_fixtures(
        MANIFEST_PATH, REPO_ROOT, verify_development_files=False
    )

    assert len(development_fixtures) == 10
    # Every visible consultation stays in the frozen order used by later baseline reports.
    assert (
        tuple(development_fixture.stem for development_fixture in development_fixtures)
        == development_corpus_helper.EXPECTED_STEMS
    )
    declared_textgrid_paths = tuple(
        textgrid_path
        for development_fixture in development_fixtures
        for textgrid_path in (
            development_fixture.doctor_textgrid_path,
            development_fixture.patient_textgrid_path,
        )
    )
    assert len(declared_textgrid_paths) == 20


def test_synthetic_manifest_verifies_ten_triplets_and_twenty_truth_files(
    tmp_path: Path,
) -> None:
    """Default pytest exercises file hashes and truth selection with temporary bytes."""
    development_corpus_helper = load_development_corpus_helper()
    manifest_path = write_synthetic_development_manifest(
        tmp_path, development_corpus_helper
    )

    development_fixtures = development_corpus_helper.load_development_fixtures(
        manifest_path, tmp_path
    )
    textgrid_paths = development_corpus_helper.development_textgrid_paths(
        manifest_path, tmp_path
    )

    assert len(development_fixtures) == 10
    assert (
        tuple(development_fixture.stem for development_fixture in development_fixtures)
        == development_corpus_helper.EXPECTED_STEMS
    )
    assert len(textgrid_paths) == 20
    assert len(set(textgrid_paths)) == 20
    assert all(textgrid_path.is_file() for textgrid_path in textgrid_paths)


@pytest.mark.skipif(
    not RUN_PROVISIONED_QUALITY_TESTS,
    reason=(
        "set AMBIENT_SCRIBE_RUN_PROVISIONED_QUALITY_TESTS=1 after provisioning "
        "the ignored development corpus"
    ),
)
def test_provisioned_manifest_verifies_real_development_triplets() -> None:
    """An explicit provisioned run verifies every frozen local corpus byte."""
    development_corpus_helper = load_development_corpus_helper()

    development_fixtures = development_corpus_helper.load_development_fixtures(
        MANIFEST_PATH, REPO_ROOT
    )
    textgrid_paths = development_corpus_helper.development_textgrid_paths(
        MANIFEST_PATH, REPO_ROOT
    )

    assert len(development_fixtures) == 10
    assert len(textgrid_paths) == 20


def test_sealed_stem_request_fails_before_fixture_selection() -> None:
    """A sealed demo choice stops before content can enter development work."""
    development_corpus_helper = load_development_corpus_helper()

    with pytest.raises(
        development_corpus_helper.DevelopmentCorpusError, match="^sealed_stem:"
    ):
        development_corpus_helper.validate_requested_stems([SEALED_SPECIMEN])


@pytest.mark.parametrize(
    ("scorer_field", "unsafe_scorer_value", "expected_error_category"),
    [
        ("path", "scripts/unapproved-scorer.py", "scorer_identity"),
        ("bytes", 99535, "scorer_size_drift"),
        ("sha256", "0" * 64, "scorer_hash_drift"),
    ],
)
def test_scorer_drift_fails_before_fixture_selection(
    tmp_path: Path,
    scorer_field: str,
    unsafe_scorer_value: object,
    expected_error_category: str,
) -> None:
    """A changed scorer stops before any consultation record can be selected."""
    development_corpus_helper = load_development_corpus_helper()
    drifted_manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    drifted_manifest["scorer"][scorer_field] = unsafe_scorer_value
    drifted_manifest_path = tmp_path / "development-corpus-with-scorer-drift.json"
    drifted_manifest_path.write_text(
        json.dumps(drifted_manifest),
        encoding="utf-8",
    )

    with mock.patch.object(
        development_corpus_helper, "_manifest_fixture_records"
    ) as fixture_record_reader:
        with pytest.raises(
            development_corpus_helper.DevelopmentCorpusError,
            match=rf"^{expected_error_category}:",
        ):
            development_corpus_helper.load_development_fixtures(
                drifted_manifest_path,
                REPO_ROOT,
            )

    fixture_record_reader.assert_not_called()


def test_manifest_registers_consult_29_cross_lane_evidence() -> None:
    """The reviewer sees consult-2.9 lane, timing, confidence, and source limits together."""
    development_manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    consult_29_fixture = development_manifest["fixtures"][6]
    consult_29_critical_span = consult_29_fixture["critical_spans"][0]

    assert development_manifest["scorer"] == {
        "version": "ambient-scribe-transcript-quality/0.5.0",
        "path": "scripts/transcript-quality.py",
        "bytes": 100065,
        "sha256": "9cf8c3a18a8089a1f5525fdb993efc74bc3910d6f05b7d3439f04226273a0426",
    }
    assert development_manifest["picker_catalog"] == {
        "path": "tests/fixtures/audio/generated-manifest.json",
        "fixture_count": 10,
        "bytes": 10846,
        "sha256": "b1eea4055ec167de4198fcbf12866bfdb14fed507a9b0a270f07eac65c261c82",
        "generation_mode": "ten_explicit_development_case_ids",
    }
    assert consult_29_fixture["fixture_id"] == (
        "primock57-day2-consultation09-i-cant-move-my-left-arm"
    )
    assert consult_29_critical_span == {
        "span_id": "consult-2.9-medication-allergy",
        "start_seconds": 293.345,
        "end_seconds": 310.436,
        "membership_rule": "time_overlap",
        "canonical_terms": ["metformin", "losartan", "amlodipine", "penicillin"],
        "expectations": {
            "live": "score retained live hypotheses without canonical reconstruction",
            "corrected": "score retained corrected hypotheses separately from live",
            "attribution": "score text and Doctor/Patient ownership independently",
            "confidence": (
                "null is unmeasured and excluded from review-marker denominators"
            ),
            "selected_source": (
                "NOT_OBSERVED until an exact selected-source artifact is retained"
            ),
            "saved_note": ("NOT_OBSERVED until an exact note/source pair is retained"),
        },
    }
    assert [
        persisted_artifact["lane"]
        # Every retained replay keeps its own lane in the visible run order.
        for persisted_artifact in consult_29_fixture["persisted_artifacts"]
    ] == ["live", "corrected", "live", "corrected", "live", "corrected"]


def registered_persisted_artifacts() -> list[dict[str, Any]]:
    """Return the tracked evidence identities without opening ignored campaign output."""
    development_manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return (
        development_manifest["fixtures"][6]["persisted_artifacts"]
        + development_manifest["fixtures"][8]["persisted_artifacts"]
    )


def test_registered_persisted_artifacts_have_reproducible_identities() -> None:
    """Tracked evidence records retain unique paths, sizes, lanes, and hashes."""
    registered_artifacts = registered_persisted_artifacts()

    assert registered_artifacts
    assert len({artifact["path"] for artifact in registered_artifacts}) == len(
        registered_artifacts
    )
    assert tuple(artifact["lane"] for artifact in registered_artifacts) == (
        "live",
        "corrected",
        "live",
        "corrected",
        "live",
        "corrected",
        "selected_source",
        "saved_note",
    )
    for registered_artifact in registered_artifacts:
        assert isinstance(registered_artifact["bytes"], int)
        assert registered_artifact["bytes"] > 0
        assert re.fullmatch(r"[0-9a-f]{64}", str(registered_artifact["sha256"]))
        assert str(registered_artifact["path"]).startswith("var/quality/")


@pytest.mark.skipif(
    not RUN_PROVISIONED_QUALITY_TESTS,
    reason=(
        "set AMBIENT_SCRIBE_RUN_PROVISIONED_QUALITY_TESTS=1 after provisioning "
        "the ignored retained quality evidence"
    ),
)
def test_provisioned_persisted_artifacts_keep_their_hashes() -> None:
    """An explicit provisioned run verifies retained cross-lane evidence bytes."""
    registered_artifacts = registered_persisted_artifacts()

    # Each registered source must still match before a clinician-facing score can cite it.
    for registered_artifact in registered_artifacts:
        registered_artifact_path = REPO_ROOT / registered_artifact["path"]
        assert registered_artifact_path.stat().st_size == registered_artifact["bytes"]
        assert (
            hashlib.sha256(registered_artifact_path.read_bytes()).hexdigest()
            == registered_artifact["sha256"]
        )


def test_generator_and_picker_catalog_expose_only_development_cases() -> None:
    """The Demo Audio picker shows the same ten visits used by the quality report."""
    demo_audio_generator = load_demo_audio_generator()
    development_corpus_helper = load_development_corpus_helper()
    picker_catalog = json.loads(PICKER_CATALOG_PATH.read_text(encoding="utf-8"))
    approved_catalog = demo_audio_generator.load_development_primock57_catalog()

    assert demo_audio_generator.PRIMOCK57_DEVELOPMENT_CASE_IDS == frozenset(
        EXPECTED_DEVELOPMENT_CASE_IDS
    )
    assert tuple(case_id for case_id, _stem in approved_catalog) == (
        EXPECTED_DEVELOPMENT_CASE_IDS
    )
    assert tuple(stem for _case_id, stem in approved_catalog) == (
        development_corpus_helper.EXPECTED_STEMS
    )
    # A sealed filename cannot appear in the user-visible development catalog.
    assert set(
        picker_row["filename"].removesuffix(".wav") for picker_row in picker_catalog
    ).isdisjoint(development_corpus_helper.SEALED_STEMS)
    assert PICKER_CATALOG_PATH.stat().st_size == 10846
    assert (
        hashlib.sha256(PICKER_CATALOG_PATH.read_bytes()).hexdigest()
        == "b1eea4055ec167de4198fcbf12866bfdb14fed507a9b0a270f07eac65c261c82"
    )
    assert len(picker_catalog) == 10
    # Picker rows keep the same case order a developer sees in quality reports.
    assert tuple(picker_row["case_id"] for picker_row in picker_catalog) == (
        EXPECTED_DEVELOPMENT_CASE_IDS
    )
    assert tuple(picker_row["filename"] for picker_row in picker_catalog) == tuple(
        f"{development_stem}.wav"
        # Each picker filename resolves to one exact development WAV, never a local extra.
        for development_stem in development_corpus_helper.EXPECTED_STEMS
    )


def test_generator_filters_sealed_case_before_note_selection() -> None:
    """A sealed case never reaches note lookup while a developer refreshes the picker."""
    demo_audio_generator = load_demo_audio_generator()
    discovered_case_files = {
        "day1_consultation02": {
            "doctor": "day1_consultation02_doctor.wav",
            "patient": "day1_consultation02_patient.wav",
        },
        "day2_consultation07": {
            "doctor": "day2_consultation07_doctor.wav",
            "patient": "day2_consultation07_patient.wav",
        },
    }

    with mock.patch.object(
        demo_audio_generator,
        "read_primock57_complaint",
        return_value="I have sore red skin",
    ) as development_note_reader:
        selected_case_files, _ = demo_audio_generator.select_primock57_case_files(
            discovered_case_files,
            limit=10,
            selected_cases={"day1_consultation02"},
        )

    assert selected_case_files == [
        (
            "day1_consultation02",
            discovered_case_files["day1_consultation02"],
        )
    ]
    development_note_reader.assert_called_once_with("day1_consultation02")


def test_generator_validates_manifest_before_remote_inventory() -> None:
    """A broken development allowlist stops before the GitHub file API is opened."""
    demo_audio_generator = load_demo_audio_generator()

    with mock.patch.object(
        demo_audio_generator,
        "load_development_primock57_catalog",
        side_effect=RuntimeError("manifest rejected"),
    ):
        with mock.patch.object(
            demo_audio_generator, "read_json_url"
        ) as remote_inventory_reader:
            with pytest.raises(RuntimeError, match="manifest rejected"):
                demo_audio_generator.discover_primock57_consultations(10, set())

    remote_inventory_reader.assert_not_called()


def test_partial_picker_refresh_preserves_complete_development_order() -> None:
    """Refreshing one approved WAV keeps all ten picker rows in frozen order."""
    demo_audio_generator = load_demo_audio_generator()
    picker_catalog = json.loads(PICKER_CATALOG_PATH.read_text(encoding="utf-8"))
    refreshed_entry = dict(picker_catalog[3])
    refreshed_entry["duration_seconds"] = 321.0

    merged_catalog = demo_audio_generator.merge_picker_catalog(
        picker_catalog,
        [refreshed_entry],
        require_complete_primock57=True,
    )

    assert len(merged_catalog) == 10
    assert tuple(entry["case_id"] for entry in merged_catalog) == (
        EXPECTED_DEVELOPMENT_CASE_IDS
    )
    assert merged_catalog[3]["duration_seconds"] == 321.0
    assert merged_catalog[:3] == picker_catalog[:3]
    assert merged_catalog[4:] == picker_catalog[4:]


def test_partial_picker_refresh_rejects_an_incomplete_existing_catalog() -> None:
    """A nine-row catalog cannot be published as a successful one-case refresh."""
    demo_audio_generator = load_demo_audio_generator()
    picker_catalog = json.loads(PICKER_CATALOG_PATH.read_text(encoding="utf-8"))

    with pytest.raises(RuntimeError, match="complete development catalog"):
        demo_audio_generator.merge_picker_catalog(
            picker_catalog[:-1],
            [picker_catalog[0]],
            require_complete_primock57=True,
        )


def test_mixed_valid_and_mistyped_selectors_stop_before_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One unknown selector rejects the whole refresh before WAV or catalog writes."""
    demo_audio_generator = load_demo_audio_generator()
    picker_catalog = json.loads(PICKER_CATALOG_PATH.read_text(encoding="utf-8"))
    (tmp_path / "generated-manifest.json").write_text(
        json.dumps(picker_catalog), encoding="utf-8"
    )
    selected_case_id = EXPECTED_DEVELOPMENT_CASE_IDS[0]
    selected_row = picker_catalog[0]
    selected_consultation = demo_audio_generator.Primock57Consultation(
        case_id=selected_case_id,
        filename=selected_row["filename"],
        complaint=selected_row["complaint"],
        doctor_url="https://example.invalid/doctor.wav",
        patient_url="https://example.invalid/patient.wav",
        source_paths=("audio/doctor.wav", "audio/patient.wav"),
        note_path=f"notes/{selected_case_id}.json",
    )
    command_arguments = mock.Mock(
        output_dir=str(tmp_path),
        case=[selected_case_id, "mistyped-case"],
        force=True,
        include_primock57=True,
        primock57_limit=10,
    )
    monkeypatch.setattr(demo_audio_generator, "parse_args", lambda: command_arguments)
    monkeypatch.setattr(demo_audio_generator.shutil, "which", lambda _name: "ffmpeg")
    monkeypatch.setattr(
        demo_audio_generator,
        "discover_primock57_consultations",
        lambda _limit, _selected_cases: [selected_consultation],
    )
    audio_generator = mock.Mock()
    catalog_writer = mock.Mock()
    monkeypatch.setattr(demo_audio_generator, "generate_primock57_wav", audio_generator)
    monkeypatch.setattr(demo_audio_generator, "write_picker_catalog", catalog_writer)

    with pytest.raises(SystemExit, match="unmatched --case selectors"):
        demo_audio_generator.main()

    audio_generator.assert_not_called()
    catalog_writer.assert_not_called()


def test_failed_catalog_replace_preserves_prior_bytes_and_cleans_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed final rename leaves the browser's prior picker catalog untouched."""
    demo_audio_generator = load_demo_audio_generator()
    manifest_path = tmp_path / "generated-manifest.json"
    prior_bytes = b'[{"filename":"prior.wav"}]\n'
    manifest_path.write_bytes(prior_bytes)

    def reject_catalog_replace(_staged_path: Path, _manifest_path: Path) -> None:
        """Model a filesystem failure after the complete catalog was staged."""
        raise OSError("catalog replace failed")

    monkeypatch.setattr(demo_audio_generator.os, "replace", reject_catalog_replace)

    with pytest.raises(OSError, match="catalog replace failed"):
        demo_audio_generator.write_picker_catalog(
            manifest_path,
            [{"filename": "replacement.wav"}],
        )

    assert manifest_path.read_bytes() == prior_bytes
    assert list(tmp_path.glob(".generated-manifest.json.*.tmp")) == []


def test_interrupted_download_preserves_existing_audio_and_cleans_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dropped remote response leaves the prior replay WAV byte-identical."""
    demo_audio_generator = load_demo_audio_generator()
    output_path = tmp_path / "approved.wav"
    output_path.write_bytes(b"prior approved audio")

    class InterruptedResponse:
        """Yield one partial chunk, then model a network failure."""

        def __enter__(self) -> InterruptedResponse:
            """Return the response object used by the downloader context."""
            return self

        def __exit__(self, *_args: object) -> None:
            """Close the fake response without suppressing the failure."""

        def read(self, _size: int = -1) -> bytes:
            """Return partial bytes once, then fail before a complete WAV exists."""
            # The first chunk reaches only the staging file, never the approved destination.
            if not hasattr(self, "chunk_returned"):
                self.chunk_returned = True
                return b"partial audio"
            raise OSError("connection dropped")

    monkeypatch.setattr(
        demo_audio_generator,
        "urlopen",
        lambda *_args, **_kwargs: InterruptedResponse(),
    )

    with pytest.raises(RuntimeError, match="could not download"):
        demo_audio_generator.download_file(
            "https://example.invalid/audio.wav", output_path
        )

    assert output_path.read_bytes() == b"prior approved audio"
    assert list(tmp_path.glob(".approved.*.tmp")) == []


@pytest.mark.parametrize("fixture_set", ["missing", "extra"])
def test_transcript_downloader_rejects_manifest_mismatch_before_curl(
    fixture_set: str, tmp_path: Path
) -> None:
    """Missing or extra local WAV names stop before any transcript URL is opened."""
    development_corpus_helper = load_development_corpus_helper()
    fixture_dir = tmp_path / "audio"
    fixture_dir.mkdir()
    approved_stems = list(development_corpus_helper.EXPECTED_STEMS)
    stems_to_create = (
        approved_stems[:-1] if fixture_set == "missing" else approved_stems
    )
    # Filename discovery is allowed, but no WAV body may be opened by a rejected run.
    for fixture_stem in stems_to_create:
        (fixture_dir / f"{fixture_stem}.wav").write_bytes(b"do not open")
    # An extra sealed-looking name proves over-broad discovery also stops before download.
    if fixture_set == "extra":
        (fixture_dir / f"{SEALED_SPECIMEN}.wav").write_bytes(b"sealed sentinel")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    curl_marker = tmp_path / "curl-was-called"
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        f"#!/usr/bin/env bash\ntouch {curl_marker}\nexit 99\n",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)
    command_environment = os.environ.copy()
    command_environment["PATH"] = f"{fake_bin}:{command_environment['PATH']}"
    command_environment["AMBIENT_SCRIBE_PRIMOCK57_FIXTURE_DIR"] = str(fixture_dir)

    completed = subprocess.run(
        [str(PRIMOCK57_TRANSCRIPT_DOWNLOADER_PATH)],
        cwd=REPO_ROOT,
        env=command_environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "development manifest mismatch" in completed.stderr
    assert not curl_marker.exists()
    assert list(fixture_dir.glob("*.TextGrid")) == []


def test_manifest_registers_sealed_hashes_without_clinical_content() -> None:
    """Reviewers can verify sealed identities without opening a consultation.
    Use before development scoring to confirm holdout metadata remains hash-only.
    """
    development_manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    reviewer_visible_registration = development_manifest["sealed_holdouts"]
    registered_triplets = reviewer_visible_registration["triplet_hashes"]
    expected_stems_by_group = {
        "primary": reviewer_visible_registration["primary"],
        "contingency": reviewer_visible_registration["contingency"],
    }
    expected_file_kinds = ["wav", "doctor_textgrid", "patient_textgrid"]
    registered_filenames: list[str] = []

    assert reviewer_visible_registration["registration_status"] == (
        "registered_prior_seal_matched"
    )
    assert reviewer_visible_registration["content_access"] == "prohibited"
    assert reviewer_visible_registration["hash_fields"] == ["bytes", "sha256"]
    assert registered_triplets["comparison_status"] == "18_of_18_matched"
    assert registered_triplets["prior_seal"] == (
        "var/quality/m07-manifests-20260715T221131Z/triplet-hashes.sha256"
    )
    assert registered_triplets["evidence"] == (
        "var/quality/0.5.0-harness-20260717T011802Z/t02.9-holdout-registration.tsv"
    )

    # Each reviewer group keeps the approved three-stem order from the human packet.
    for holdout_group, expected_stems in expected_stems_by_group.items():
        visible_group_triplets = registered_triplets[holdout_group]
        # The visible stem list must match the corresponding registered triplets exactly.
        assert [
            registered_triplet["stem"]
            # Each stored stem is an identity label, never extracted clinical wording.
            for registered_triplet in visible_group_triplets
        ] == expected_stems

        # Each approved visit exposes only its three file identities, sizes, and digests.
        for registered_triplet in visible_group_triplets:
            visible_file_records = registered_triplet["files"]
            # File-kind order mirrors the approved WAV, Doctor, Patient hash operation.
            assert [
                visible_file_record["kind"]
                # Each kind tells reviewers which sealed member the digest identifies.
                for visible_file_record in visible_file_records
            ] == expected_file_kinds

            # Each file record must remain metadata-only and cryptographically usable.
            for visible_file_record in visible_file_records:
                assert set(visible_file_record) == {
                    "kind",
                    "filename",
                    "bytes",
                    "sha256",
                }
                assert visible_file_record["bytes"] > 0
                assert re.fullmatch(r"[0-9a-f]{64}", visible_file_record["sha256"])
                registered_filenames.append(visible_file_record["filename"])

    assert len(registered_filenames) == 18
    assert len(set(registered_filenames)) == 18
