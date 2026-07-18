"""Protect the developer's 0.5.0 consultation picker and evaluation inputs.

Use these CPU-only tests before a baseline or vocabulary sweep can open local
fixtures. They prove the helper exposes exactly ten approved consultations and
rejects a sealed choice without reading its content.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER_PATH = REPO_ROOT / "scripts" / "development-corpus.py"
DEMO_AUDIO_GENERATOR_PATH = REPO_ROOT / "scripts/generate-demo-consultation-audio.py"
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


def test_manifest_resolves_exactly_ten_development_triplets() -> None:
    """The developer gets ten ordered cases before starting a quality run."""
    development_corpus_helper = load_development_corpus_helper()

    development_fixtures = development_corpus_helper.load_development_fixtures(
        MANIFEST_PATH, REPO_ROOT
    )

    assert len(development_fixtures) == 10
    # Every visible consultation stays in the frozen order used by later baseline reports.
    assert (
        tuple(development_fixture.stem for development_fixture in development_fixtures)
        == development_corpus_helper.EXPECTED_STEMS
    )
    assert (
        len(
            development_corpus_helper.development_textgrid_paths(
                MANIFEST_PATH, REPO_ROOT
            )
        )
        == 20
    )


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
        "bytes": 99534,
        "sha256": "bb91823432e1103c4bf3f3c5f12f2f7c3cacff7afef3ddaf20148a528685ee1e",
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


def test_registered_persisted_artifacts_keep_their_hashes() -> None:
    """The reviewer gets the same saved rows whenever a cross-lane result is reproduced."""
    development_manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    registered_artifacts = (
        development_manifest["fixtures"][6]["persisted_artifacts"]
        + development_manifest["fixtures"][8]["persisted_artifacts"]
    )

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

    assert demo_audio_generator.PRIMOCK57_DEVELOPMENT_CASE_IDS == frozenset(
        EXPECTED_DEVELOPMENT_CASE_IDS
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
