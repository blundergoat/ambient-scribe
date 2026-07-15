"""Tests for medical-term correction used by clinician-visible transcripts.

The suite keeps the post-ASR fallback honest while decode-time NeMo boosting
waits for GPU-container proof. It covers the safety review table, exact visible
text replacement, and the pipeline seam that feeds the browser and summaries.
"""

from __future__ import annotations

import json
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

from medical_lexicon import (
    MedicalLexiconMatch,
    MedicalPhrase,
    correct_medical_terms,
    correct_medical_terms_with_audit,
    default_medical_lexicon_path,
    load_medical_lexicon,
)
from api.summary_request import transcript_text_from_segments
from nemo_pipeline import NemoPipeline

REPO_ROOT = Path(__file__).resolve().parents[2]
REVIEW_PATH = REPO_ROOT / "strands_agents" / "data" / "medical_lexicon_review.json"
EVALUATOR_PATH = REPO_ROOT / "scripts" / "evaluate-medical-boost.py"


def load_medical_boost_evaluator() -> ModuleType:
    """Load the CPU evaluator so tests can call validation helpers directly."""
    spec = importlib.util.spec_from_file_location(
        "ambient_scribe_medical_boost_evaluator",
        EVALUATOR_PATH,
    )
    assert spec is not None
    assert spec.loader is not None

    evaluator = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = evaluator
    spec.loader.exec_module(evaluator)
    return evaluator


def test_missing_lexicon_loads_no_entries(tmp_path):
    """Missing lexicons leave the transcript unchanged for the user."""
    assert load_medical_lexicon(tmp_path / "missing.txt") == ()


def test_unreadable_lexicon_loads_no_entries(tmp_path, caplog):
    """Unreadable lexicons leave the transcript unchanged and warn operators."""
    assert load_medical_lexicon(tmp_path) == ()
    assert "medical_lexicon.unavailable" in caplog.text


def test_invalid_utf8_lexicon_loads_no_entries(tmp_path, caplog):
    """Invalid local lexicon bytes do not block the user's recording."""
    lexicon_path = tmp_path / "medical_lexicon.txt"
    lexicon_path.write_bytes(b"\xff")

    assert load_medical_lexicon(lexicon_path) == ()
    assert "medical_lexicon.unavailable" in caplog.text


def test_loads_canonical_terms_and_variants(tmp_path):
    """Lexicon rows become ordered correction entries for transcript display."""
    lexicon_path = tmp_path / "medical_lexicon.txt"
    lexicon_path.write_text(
        "# demo terms\nnaproxen|na proxen|naproxin\nlisinopril|lisin april\n",
        encoding="utf-8",
    )

    entries = load_medical_lexicon(lexicon_path)

    assert entries[0].canonical == "naproxen"
    assert entries[0].variants == ("naproxen", "na proxen", "naproxin")
    assert entries[1].canonical == "lisinopril"


def test_malformed_rows_are_ignored_without_blocking_transcription(tmp_path, caplog):
    """Empty canonical rows are skipped so variants cannot invent UI terms."""
    lexicon_path = tmp_path / "medical_lexicon.txt"
    lexicon_path.write_text("|orphan variant\nnaproxen|na proxen\n", encoding="utf-8")

    entries = load_medical_lexicon(lexicon_path)

    assert [entry.canonical for entry in entries] == ["naproxen"]
    assert "medical_lexicon.empty_canonical" in caplog.text


def test_duplicate_variants_warn_and_keep_first_visible_spelling(tmp_path, caplog):
    """Duplicate variants are flagged so reviewers can clean the row."""
    lexicon_path = tmp_path / "medical_lexicon.txt"
    lexicon_path.write_text("naproxen|na proxen|na proxen\n", encoding="utf-8")

    entries = load_medical_lexicon(lexicon_path)

    assert entries[0].variants == ("naproxen", "na proxen")
    assert "medical_lexicon.duplicate_variant" in caplog.text


def test_correct_medical_terms_replaces_exact_phrase_variants(tmp_path):
    """Known ASR variants are normalised without touching unrelated words."""
    lexicon_path = tmp_path / "medical_lexicon.txt"
    lexicon_path.write_text(
        "naproxen|na proxen\nlisinopril|lisin april\n", encoding="utf-8"
    )
    phrases = load_medical_lexicon(lexicon_path)

    corrected_text = correct_medical_terms(
        "The patient takes na proxen and lisin april.",
        phrases,
    )

    assert corrected_text == "The patient takes naproxen and lisinopril."


def test_correct_medical_terms_preserves_sentence_initial_capitalization(tmp_path):
    """Sentence starts keep their capital letter in the clinician transcript."""
    lexicon_path = tmp_path / "medical_lexicon.txt"
    lexicon_path.write_text("naproxen|na proxen\n", encoding="utf-8")
    phrases = load_medical_lexicon(lexicon_path)

    corrected_text = correct_medical_terms(
        "Naproxen helped. Na proxen helped.", phrases
    )

    assert corrected_text == "Naproxen helped. Naproxen helped."


def test_correct_medical_terms_handles_punctuation_and_repeated_matches():
    """Punctuation and repeated ASR variants still become readable transcript text."""
    phrases = (MedicalPhrase(canonical="naproxen", variants=("naproxen", "na proxen")),)

    correction = correct_medical_terms_with_audit(
        "Use na proxen, not na   proxen?",
        phrases,
    )

    assert correction.corrected_text == "Use naproxen, not naproxen?"
    assert correction.matches == (
        MedicalLexiconMatch(canonical="naproxen", variant="na proxen", count=2),
    )


def test_correct_medical_terms_does_not_replace_inside_unrelated_words():
    """Exact word boundaries keep ordinary speech from becoming clinical terms."""
    phrases = (MedicalPhrase(canonical="metoprolol", variants=("metro pro lol",)),)

    corrected_text = correct_medical_terms(
        "The metro pro lollipop was unrelated.", phrases
    )

    assert corrected_text == "The metro pro lollipop was unrelated."


def test_default_lexicon_keeps_disabled_synonyms_and_plurals_raw():
    """Risky prior rows stay inactive until a clinician reviewer accepts them."""
    phrases = load_medical_lexicon(default_medical_lexicon_path())

    assert (
        correct_medical_terms(
            "The patient said heart attack in their own words.", phrases
        )
        == "The patient said heart attack in their own words."
    )
    assert (
        correct_medical_terms(
            "Please repeat thyroid function tests next month.", phrases
        )
        == "Please repeat thyroid function tests next month."
    )
    assert (
        correct_medical_terms("Please listen April is speaking.", phrases)
        == "Please listen April is speaking."
    )


def test_review_table_classifies_every_active_lexicon_row():
    """Every active visible correction has a reviewer category and guard."""
    phrases = load_medical_lexicon(default_medical_lexicon_path())
    payload = json.loads(REVIEW_PATH.read_text(encoding="utf-8"))
    review_rows = payload["entries"]

    active_rows = {
        str(row["canonical"]): row
        for row in review_rows
        if row.get("status") == "active"
    }

    assert {phrase.canonical for phrase in phrases} == set(active_rows)
    assert all(row["category"] != "semantic_synonym" for row in active_rows.values())
    assert all(row.get("false_positive_guard") for row in active_rows.values())


def test_medical_boost_eval_script_scores_review_table():
    """The CPU evaluator prints the before/after table used for M14 evidence."""
    completed = subprocess.run(
        [sys.executable, str(EVALUATOR_PATH)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "metro pro lol" in completed.stdout
    assert "heart attack" in completed.stdout
    assert "thyroid function tests" in completed.stdout


def test_medical_boost_eval_requires_metadata_and_active_coverage():
    """Reviewer rows need provenance and must match active visible corrections."""
    evaluator = load_medical_boost_evaluator()
    incomplete_review = evaluator.ReviewEntry(
        canonical="metoprolol",
        status="active",
        category="asr_variant",
        raw_phrase="metro pro lol",
        expected_visible_phrase="metoprolol",
        false_positive_guard="The metro prologue was unrelated.",
        provenance="",
        safety_rationale="",
    )
    lexicon_phrases = (
        MedicalPhrase(canonical="metoprolol", variants=("metro pro lol",)),
        MedicalPhrase(canonical="naproxen", variants=("na proxen",)),
    )

    structural_issues = evaluator.validate_review_entries([incomplete_review])
    coverage_issues = evaluator.validate_lexicon_review_coverage(
        [incomplete_review],
        lexicon_phrases,
    )

    assert structural_issues == ["metoprolol: missing required review field"]
    assert coverage_issues == ["active lexicon rows missing review entries: naproxen"]


def test_empty_phrases_keep_transcript_text_unchanged():
    """No phrase entries means the UI sees raw ASR output."""
    assert correct_medical_terms("plain transcript", ()) == "plain transcript"


def test_pipeline_applies_medical_correction_at_parse_seam(tmp_path, monkeypatch):
    """The correction stays inside NemoPipeline so callers do not change."""
    lexicon_path = tmp_path / "medical_lexicon.txt"
    lexicon_path.write_text("metoprolol|metro pro lol\n", encoding="utf-8")
    monkeypatch.setenv("MEDICAL_BOOST_ENABLED", "1")
    monkeypatch.setenv("MEDICAL_LEXICON_PATH", str(lexicon_path))

    pipeline = NemoPipeline()
    hypothesis = type("Hypothesis", (), {"text": "continue metro pro lol daily"})()
    segments = pipeline._parse_nemo_output([["0.0 2.0 speaker_0"]], [hypothesis])

    assert segments[0].text == "continue metoprolol daily"


def test_pipeline_toggle_controls_summary_visible_text(tmp_path, monkeypatch):
    """Summaries receive the same visible row text the clinician reviews."""
    lexicon_path = tmp_path / "medical_lexicon.txt"
    lexicon_path.write_text("metoprolol|metro pro lol\n", encoding="utf-8")
    monkeypatch.setenv("MEDICAL_LEXICON_PATH", str(lexicon_path))
    hypothesis = type("Hypothesis", (), {"text": "continue metro pro lol daily"})()

    monkeypatch.setenv("MEDICAL_BOOST_ENABLED", "0")
    raw_pipeline = NemoPipeline()
    raw_segments = raw_pipeline._parse_nemo_output(
        [["0.0 2.0 speaker_0"]], [hypothesis]
    )

    monkeypatch.setenv("MEDICAL_BOOST_ENABLED", "1")
    corrected_pipeline = NemoPipeline()
    corrected_segments = corrected_pipeline._parse_nemo_output(
        [["0.0 2.0 speaker_0"]],
        [hypothesis],
    )

    assert raw_segments[0].text == "continue metro pro lol daily"
    assert corrected_segments[0].text == "continue metoprolol daily"
    assert "continue metoprolol daily" in transcript_text_from_segments(
        [{"role": "PATIENT", **corrected_segments[0].dict()}]
    )


def test_medical_correction_is_on_by_default(tmp_path, monkeypatch):
    """An unconfigured deployment fixes misheard terms without any env setup."""
    lexicon_path = tmp_path / "medical_lexicon.txt"
    lexicon_path.write_text("metoprolol|metro pro lol\n", encoding="utf-8")
    monkeypatch.delenv("MEDICAL_BOOST_ENABLED", raising=False)
    monkeypatch.setenv("MEDICAL_LEXICON_PATH", str(lexicon_path))

    pipeline = NemoPipeline()
    hypothesis = type("Hypothesis", (), {"text": "continue metro pro lol daily"})()
    segments = pipeline._parse_nemo_output([["0.0 2.0 speaker_0"]], [hypothesis])

    assert segments[0].text == "continue metoprolol daily"


def test_explicit_zero_still_disables_medical_correction(tmp_path, monkeypatch):
    """A deployment that opts out with 0 keeps raw ASR text."""
    lexicon_path = tmp_path / "medical_lexicon.txt"
    lexicon_path.write_text("metoprolol|metro pro lol\n", encoding="utf-8")
    monkeypatch.setenv("MEDICAL_BOOST_ENABLED", "0")
    monkeypatch.setenv("MEDICAL_LEXICON_PATH", str(lexicon_path))

    pipeline = NemoPipeline()
    hypothesis = type("Hypothesis", (), {"text": "continue metro pro lol daily"})()
    segments = pipeline._parse_nemo_output([["0.0 2.0 speaker_0"]], [hypothesis])

    assert segments[0].text == "continue metro pro lol daily"


# --- M04: consult-1.2 medication variants (observed in the corrected lane) ---

# One observed misspelling per canonical, exactly as the corrected rows stored
# them (m02-acceptance replay). These are the four clinician-reviewed targets.
CONSULT12_POSITIVE_FIXTURES = [
    ("like Luratidine or", "like Loratadine or"),
    (
        "Pyritin, which can help with the itchiness",
        "Piriton, which can help with the itchiness",
    ),
    (
        "Um something like Fexaphenidine, which I",
        "Um something like Fexofenadine, which I",
    ),
    (
        "I certainly think using the steroids and the emolons um on a regular",
        "I certainly think using the steroids and the emollients um on a regular",
    ),
]

# Wording that must never be rewritten: ordinary language near-collisions,
# already-canonical forms, different substances, and ambiguous product talk.
CONSULT12_HARD_NEGATIVES = [
    "There was pyrite in the rock sample.",
    "The peritoneum looked normal.",
    "She brought melons from the market.",
    "Loratadine once daily is working well.",
    "Piriton was taken last night.",
    "Fexofenadine hydrochloride 180mg.",
    "Keep applying the emollients daily.",
    "Try an over-the-counter steroid cream first.",
    "Loperamide settled the stomach upset.",
]


def test_consult12_variants_normalize_with_punctuation_and_case_preserved():
    """Each observed misspelling becomes its reviewed canonical, nothing else moves."""
    phrases = load_medical_lexicon(default_medical_lexicon_path())

    for observed_row_text, expected_visible_text in CONSULT12_POSITIVE_FIXTURES:
        assert (
            correct_medical_terms(observed_row_text, phrases) == expected_visible_text
        )


def test_consult12_hard_negatives_stay_byte_identical():
    """Ordinary words, canonical forms, and ambiguous product talk never rewrite."""
    phrases = load_medical_lexicon(default_medical_lexicon_path())

    for hard_negative_text in CONSULT12_HARD_NEGATIVES:
        assert correct_medical_terms(hard_negative_text, phrases) == hard_negative_text


def test_consult12_variant_sweep_finds_no_collision_in_official_corpus():
    """The retained ambiguity sweep: no new variant is a word anyone actually said.

    Every official PriMock57 TextGrid is the ground-truth of what was really
    spoken. A candidate variant that appears there would mean the "misspelling"
    is a real word or another medicine, and rewriting it would corrupt a
    faithful transcript. The four canonicals must also exist in the consult-1.2
    doctor ground truth, proving the targets are the clinician's actual words.
    """
    import re as sweep_re

    audio_fixture_dir = REPO_ROOT / "tests" / "fixtures" / "audio"
    corpus_words: set[str] = set()
    # Every TextGrid text interval contributes its spoken words to the vocabulary.
    for textgrid_path in sorted(audio_fixture_dir.glob("*.TextGrid")):
        textgrid_content = textgrid_path.read_text(encoding="utf-8", errors="replace")
        for spoken_text in sweep_re.findall(r'text = "([^"]*)"', textgrid_content):
            corpus_words.update(
                word.casefold() for word in sweep_re.findall(r"[A-Za-z']+", spoken_text)
            )

    assert corpus_words, "the official corpus must be present for the sweep"

    new_variant_words = ["luratidine", "pyritin", "fexaphenidine", "emolons"]
    # Zero unsafe rewrites: none of the candidate variants is real spoken language.
    for variant_word in new_variant_words:
        assert variant_word not in corpus_words, (
            f"variant '{variant_word}' collides with real corpus speech"
        )

    consult12_doctor_grid = (
        (
            audio_fixture_dir
            / "primock57-day1-consultation02-i-have-sore-red-skin.doctor.TextGrid"
        )
        .read_text(encoding="utf-8", errors="replace")
        .casefold()
    )
    # The canonical targets are exactly what the doctor really said.
    for canonical_word in ["loratadine", "piriton", "fexofenadine", "emollients"]:
        assert canonical_word in consult12_doctor_grid
