"""
Tests for medical-term correction used by clinician-visible transcripts.

The suite keeps the M11 fallback honest while decode-time NeMo boosting waits
for GPU-container proof. It covers missing lexicons, exact phrase replacement,
and the pipeline seam that applies corrections before transcript text reaches
the browser.
"""

from medical_lexicon import correct_medical_terms, load_medical_lexicon
from nemo_pipeline import NemoPipeline


def test_missing_lexicon_loads_no_entries(tmp_path):
    """Missing lexicons leave the transcript unchanged for the user."""
    assert load_medical_lexicon(tmp_path / "missing.txt") == ()


def test_loads_canonical_terms_and_variants(tmp_path):
    """Lexicon rows become ordered correction entries for transcript display."""
    lexicon_path = tmp_path / "medical_lexicon.txt"
    lexicon_path.write_text(
        "# demo terms\nnaproxen|na proxen|naproxin\nlisinopril|listen april\n",
        encoding="utf-8",
    )

    entries = load_medical_lexicon(lexicon_path)

    assert entries[0].canonical == "naproxen"
    assert entries[0].variants == ("naproxen", "na proxen", "naproxin")
    assert entries[1].canonical == "lisinopril"


def test_correct_medical_terms_replaces_exact_phrase_variants(tmp_path):
    """Known ASR variants are normalised without touching unrelated words."""
    lexicon_path = tmp_path / "medical_lexicon.txt"
    lexicon_path.write_text(
        "naproxen|na proxen\nlisinopril|listen april\n", encoding="utf-8"
    )
    phrases = load_medical_lexicon(lexicon_path)

    corrected_text = correct_medical_terms(
        "The patient takes na proxen and listen april.",
        phrases,
    )

    assert corrected_text == "The patient takes naproxen and lisinopril."


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
