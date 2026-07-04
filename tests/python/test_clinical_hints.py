"""
Tests for the CPU-only clinical RAG and hints helpers.

These cover the M12 assistive layer without Mercure, browsers, or GPU models.
The user-facing contract is that blank transcripts stay quiet, matched KB
snippets ground summaries, and hints remain short review suggestions.
"""

from pathlib import Path

from clinical_hints import (
    generate_clinical_hints,
    load_clinical_knowledge,
    retrieve_clinical_context,
)


def test_retrieve_clinical_context_matches_transcript_keywords():
    """Relevant KB snippets are returned for terms visible in the transcript."""
    entries = [
        {
            "id": "renal",
            "title": "Renal review",
            "keywords": ["naproxen", "lisinopril"],
            "snippet": "Document renal-risk review.",
            "provenance": "test corpus",
        },
        {
            "id": "other",
            "title": "Other",
            "keywords": ["asthma"],
            "snippet": "Not matched.",
            "provenance": "test corpus",
        },
    ]

    snippets = retrieve_clinical_context(
        "Patient uses naproxen with lisinopril.",
        knowledge_entries=entries,
    )

    assert snippets == [
        {
            "id": "renal",
            "title": "Renal review",
            "snippet": "Document renal-risk review.",
            "provenance": "test corpus",
        }
    ]


def test_retrieve_clinical_context_is_empty_for_blank_transcript():
    """Blank transcripts give the summary agent no extra context."""
    assert retrieve_clinical_context("   ") == []


def test_load_clinical_knowledge_ignores_invalid_json(tmp_path):
    """Broken PoC KB files leave the summary path ungrounded, not failed."""
    knowledge_path = tmp_path / "clinical_knowledge.json"
    knowledge_path.write_text("{not-json", encoding="utf-8")

    assert load_clinical_knowledge(knowledge_path) == []


def test_generate_clinical_hints_flags_drug_interaction():
    """Medication-risk hints stay structured for the browser sidebar."""
    hints = generate_clinical_hints(
        "Doctor: I am prescribing naproxen while you continue lisinopril."
    )

    assert any(hint["type"] == "drug_interaction" for hint in hints)


def test_generate_clinical_hints_flags_missing_chest_pain_objective():
    """Chest-pain transcripts without objective assessment produce a note-gap hint."""
    hints = generate_clinical_hints("Patient: I have chest pain today.")

    assert hints == [
        {
            "type": "missing_objective",
            "text": "Consider documenting objective cardiac assessment such as ECG or vitals.",
            "evidence_span": "chest pain",
        }
    ]


def test_generate_clinical_hints_is_empty_for_blank_transcript():
    """No transcript means the browser sidebar stays hidden."""
    assert generate_clinical_hints("") == []


def test_new_clinical_helpers_do_not_import_gpu_stack():
    """RAG and hints must stay off the NeMo GPU lane."""
    project_root = Path(__file__).resolve().parents[2]
    helper_sources = [
        project_root / "strands_agents" / "clinical_hints.py",
        project_root / "strands_agents" / "medical_lexicon.py",
    ]

    for helper_source in helper_sources:
        source_text = helper_source.read_text(encoding="utf-8")
        assert "import torch" not in source_text
        assert "import nemo" not in source_text
