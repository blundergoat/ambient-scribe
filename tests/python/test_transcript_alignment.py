"""Tests for deterministic, timestamp-independent transcript alignment."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ALIGNMENT_PATH = REPO_ROOT / "scripts/transcript_alignment.py"
TRANSCRIPT_QUALITY_PATH = REPO_ROOT / "scripts/transcript-quality.py"


def load_module(name: str, path: Path) -> ModuleType:
    """Load one CPU-only script module without changing import paths."""
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


@pytest.fixture
def alignment() -> ModuleType:
    """Return the exact shared alignment module under test."""
    return load_module("transcript_alignment_test_module", ALIGNMENT_PATH)


def reference_word(
    alignment: ModuleType,
    token: str,
    ordinal: int,
    *,
    role: str = "DOCTOR",
) -> object:
    """Build one stable reference word for a focused alignment case."""
    return alignment.ReferenceWord(
        token=token,
        role=role,
        interval_ordinal=ordinal,
        word_ordinal=1,
        interval_start=float(ordinal),
        interval_end=float(ordinal + 1),
    )


def hypothesis_word(
    alignment: ModuleType,
    token: str,
    ordinal: int,
    *,
    role: str = "DOCTOR",
) -> object:
    """Build one display word without consulting its timestamp."""
    return alignment.HypothesisWord(
        token=token,
        speaker_id="speaker_0",
        role=role,
        row_index=ordinal,
        word_index=0,
        row_start=float(ordinal),
        row_end=float(ordinal) + 0.5,
        floor_span=False,
    )


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Hello, WORLD!",
        "can't won't patient's",
        "<UNSURE>certain </UNSURE> skin-cream 42",
        "O'Brien's—eczema",
    ],
)
def test_normalization_matches_existing_transcript_quality(
    alignment: ModuleType,
    text: str,
) -> None:
    """The shared evaluator must not introduce a second token vocabulary."""
    transcript_quality = load_module(
        "transcript_quality_normalization_test_module",
        TRANSCRIPT_QUALITY_PATH,
    )

    assert alignment.normalize_words(text) == transcript_quality.tokens(text)


@pytest.mark.parametrize(
    ("reference", "hypothesis"),
    [
        ([], []),
        (["alpha"], []),
        ([], ["alpha"]),
        (["alpha", "beta"], ["alpha", "gamma"]),
        (
            ["one", "two", "three", "four"],
            ["zero", "one", "three", "five"],
        ),
        (["yes", "yes", "doctor"], ["yes", "patient", "doctor"]),
    ],
)
def test_word_error_alignment_matches_existing_scorer_counts(
    alignment: ModuleType,
    reference: list[str],
    hypothesis: list[str],
) -> None:
    """Shared S/I/D uses the existing scorer's standard deterministic contract."""
    transcript_quality = load_module(
        "transcript_quality_word_error_test_module",
        TRANSCRIPT_QUALITY_PATH,
    )

    expected = transcript_quality.word_error_score(reference, hypothesis)
    result = alignment.word_error_alignment(reference, hypothesis)

    assert (
        result["substitutions"],
        result["insertions"],
        result["deletions"],
        result["total_errors"],
    ) == (
        expected.substitutions,
        expected.insertions,
        expected.deletions,
        expected.total_errors,
    )
    assert len(result["operations"]) == len(reference) + result["insertions"]


def test_unique_exact_matches_expose_stable_indices(
    alignment: ModuleType,
) -> None:
    """Only the extra display word is an insertion between two forced matches."""
    reference = [
        reference_word(alignment, "hello", 1),
        reference_word(alignment, "there", 2, role="PATIENT"),
    ]
    hypothesis = [
        hypothesis_word(alignment, "hello", 0),
        hypothesis_word(alignment, "brave", 1),
        hypothesis_word(alignment, "there", 2, role="PATIENT"),
    ]

    result = alignment.align_transcript_words(reference, hypothesis)

    assert result["unambiguous_match_count"] == 2
    assert result["reference_accounting"] == {"exact_unambiguous": 2}
    assert result["hypothesis_accounting"] == {
        "exact_unambiguous": 2,
        "inserted": 1,
    }
    assert [
        (
            match["reference"]["reference_id"],
            match["hypothesis"]["hypothesis_id"],
        )
        for match in result["matches"]
    ] == [
        (["DOCTOR", 1, 1], [0, 0]),
        (["PATIENT", 2, 1], [2, 0]),
    ]


def test_repeated_word_with_multiple_optimal_owners_stays_ambiguous(
    alignment: ModuleType,
) -> None:
    """A repeated reference word cannot be assigned to one speaker by tie-break."""
    reference = [
        reference_word(alignment, "yes", 1),
        reference_word(alignment, "yes", 2),
    ]
    hypothesis = [hypothesis_word(alignment, "yes", 0)]

    result = alignment.align_transcript_words(reference, hypothesis)

    assert result["lcs_length"] == 1
    assert result["unambiguous_match_count"] == 0
    assert result["ambiguous_rank_count"] == 1
    assert result["reference_accounting"] == {"ambiguous_exact": 2}
    assert result["hypothesis_accounting"] == {"ambiguous_exact": 1}


def test_substitution_accounting_is_deterministic(
    alignment: ModuleType,
) -> None:
    """A mismatched word is reported rather than smuggled into exact coverage."""
    reference = [
        reference_word(alignment, "alpha", 1),
        reference_word(alignment, "beta", 2),
    ]
    hypothesis = [
        hypothesis_word(alignment, "alpha", 0),
        hypothesis_word(alignment, "gamma", 1),
    ]

    first = alignment.align_transcript_words(reference, hypothesis)
    second = alignment.align_transcript_words(reference, hypothesis)

    assert first == second
    assert first["reference_accounting"] == {
        "exact_unambiguous": 1,
        "substituted": 1,
    }
    assert first["hypothesis_accounting"] == {
        "exact_unambiguous": 1,
        "substituted": 1,
    }
    assert first["selected_operation_counts"] == {
        "match": 1,
        "substitution": 1,
        "insertion": 0,
        "deletion": 0,
    }


def test_reference_overlap_words_are_excluded_from_ordering(
    alignment: ModuleType,
    tmp_path: Path,
) -> None:
    """Cross-channel interval words never gain a fabricated global word order."""
    doctor_path = tmp_path / "visit.doctor.TextGrid"
    patient_path = tmp_path / "visit.patient.TextGrid"
    doctor_path.write_text(
        'xmin = 0\nxmax = 2\ntext = "doctor overlap"\n'
        'xmin = 4\nxmax = 5\ntext = "clear doctor"\n',
        encoding="utf-8",
    )
    patient_path.write_text(
        'xmin = 1\nxmax = 3\ntext = "patient overlap"\n'
        'xmin = 6\nxmax = 7\ntext = "clear patient"\n',
        encoding="utf-8",
    )

    corpus = alignment.build_reference_corpus(
        doctor_path,
        patient_path,
        cutoff_seconds=7.0,
    )

    assert [word.token for word in corpus.alignable_words] == [
        "clear",
        "doctor",
        "clear",
        "patient",
    ]
    assert [word.token for word in corpus.overlap_ambiguous_words] == [
        "doctor",
        "overlap",
        "patient",
        "overlap",
    ]
    assert [word.identity for word in corpus.alignable_words] == [
        ("DOCTOR", 2, 1),
        ("DOCTOR", 2, 2),
        ("PATIENT", 2, 1),
        ("PATIENT", 2, 2),
    ]


def test_flattening_preserves_display_order_and_marks_floor_span(
    alignment: ModuleType,
) -> None:
    """History order and row-local indices remain stable without a time sort."""
    words = alignment.flatten_hypothesis_rows(
        [
            {
                "text": "Later row",
                "speaker_id": "speaker_1",
                "role": "patient",
                "start": 10.0,
                "end": 10.05,
            },
            {
                "text": "Earlier clock",
                "speaker_id": "speaker_0",
                "role": "doctor",
                "start": 1.0,
                "end": 2.0,
            },
        ]
    )

    assert [word.token for word in words] == [
        "later",
        "row",
        "earlier",
        "clock",
    ]
    assert [word.identity for word in words] == [(0, 0), (0, 1), (1, 0), (1, 1)]
    assert [word.floor_span for word in words] == [True, True, False, False]


def test_role_channels_accept_interleaving_without_fabricating_reference_order(
    alignment: ModuleType,
) -> None:
    """Independent role streams can own words interleaved in display order."""
    doctor_words = [
        reference_word(alignment, "alpha", 1),
        reference_word(alignment, "gamma", 2),
    ]
    patient_words = [
        reference_word(alignment, "beta", 1, role="PATIENT"),
        reference_word(alignment, "delta", 2, role="PATIENT"),
    ]
    hypothesis_words = [
        hypothesis_word(alignment, "alpha", 0),
        hypothesis_word(alignment, "beta", 1, role="PATIENT"),
        hypothesis_word(alignment, "gamma", 2),
        hypothesis_word(alignment, "delta", 3, role="PATIENT"),
    ]

    result = alignment.align_role_channels(
        {"DOCTOR": doctor_words, "PATIENT": patient_words},
        hypothesis_words,
    )

    assert result["unambiguous_match_count"] == 4
    assert result["cross_role_ambiguous_count"] == 0
    assert [
        (match["alignment_role"], match["hypothesis_index"])
        for match in result["matches"]
    ] == [
        ("DOCTOR", 0),
        ("PATIENT", 1),
        ("DOCTOR", 2),
        ("PATIENT", 3),
    ]


def test_role_channels_reject_cross_role_word_conflict(
    alignment: ModuleType,
) -> None:
    """One display word matching both role channels stays explicitly unscored."""
    result = alignment.align_role_channels(
        {
            "DOCTOR": [reference_word(alignment, "yes", 1)],
            "PATIENT": [
                reference_word(alignment, "yes", 1, role="PATIENT"),
            ],
        },
        [hypothesis_word(alignment, "yes", 0)],
    )

    assert result["unambiguous_match_count"] == 0
    assert result["cross_role_ambiguous_count"] == 1
    assert result["hypothesis_accounting"] == {"cross_role_ambiguous": 1}
    assert result["ambiguous_hypothesis_words"] == [
        {
            "classification": "cross_role_ambiguous",
            "hypothesis_index": 0,
            "possible_roles": ["DOCTOR", "PATIENT"],
            "hypothesis": {
                "token": "yes",
                "speaker_id": "speaker_0",
                "visible_role": "DOCTOR",
                "hypothesis_id": [0, 0],
            },
        }
    ]
    assert result["matches"] == []


def test_role_channels_classify_ambiguity_in_both_roles_as_cross_role(
    alignment: ModuleType,
) -> None:
    """A repeated word ambiguous in both channels has cross-role uncertainty."""
    result = alignment.align_role_channels(
        {
            "DOCTOR": [
                reference_word(alignment, "yes", 1),
                reference_word(alignment, "yes", 2),
            ],
            "PATIENT": [
                reference_word(alignment, "yes", 1, role="PATIENT"),
                reference_word(alignment, "yes", 2, role="PATIENT"),
            ],
        },
        [hypothesis_word(alignment, "yes", 0)],
    )

    assert result["unambiguous_match_count"] == 0
    assert result["cross_role_ambiguous_count"] == 1
    assert result["within_role_ambiguous_count"] == 0
    assert result["hypothesis_accounting"] == {"cross_role_ambiguous": 1}
    assert result["ambiguous_hypothesis_words"][0]["possible_roles"] == [
        "DOCTOR",
        "PATIENT",
    ]
