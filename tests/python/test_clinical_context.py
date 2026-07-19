"""
Tests for the CPU-only clinical context helpers.

These cover the summary-grounding layer without Mercure, browsers, providers,
or GPU models. The user-facing contract is that context stays off unless an
internal experiment enables a reviewed schema-valid asset.
"""

import hashlib
import json
from pathlib import Path

import clinical_context as clinical_context_module
from clinical_context import load_clinical_knowledge, retrieve_clinical_context


def _schema_eligible_knowledge_document() -> dict:
    """Return one reviewed mock card for an explicitly enabled internal probe."""
    return {
        "schema_version": "ambient-scribe-clinical-knowledge/v1",
        "asset_id": "mock-clinical-knowledge",
        "asset_version": "m01-test-v1",
        "default_state": "inactive",
        "entries": [
            {
                "id": "renal-review",
                "status": "active",
                "title": "Renal review",
                "keywords": ["lisinopril", "naproxen"],
                "snippet": "Document renal-risk review.",
                "hard_negatives": [
                    "The user is reviewing an unrelated appointment.",
                    "The weather report contains no medication discussion.",
                ],
                "evidence_class": "project_authored_documentation_checklist",
                "locale": "en-AU",
                "intended_consumer": "summary_prompt_context",
                "source": {
                    "artifact_id": "mock-documentation-source",
                    "locator": "docs/mock-documentation-source.md#renal-review",
                    "sha256": "1" * 64,
                    "published_at": "2026-07-01",
                    "updated_at": "2026-07-01",
                },
                "review": {
                    "status": "approved",
                    "reviewer_id": "mock-clinician-001",
                    "reviewer_role": "clinician",
                    "reviewed_at": "2026-07-17",
                },
                "privacy_exclusions": [
                    "contact_details",
                    "credentials_or_secrets",
                    "patient_identity",
                    "sealed_holdout_content",
                ],
            }
        ],
    }


def test_retrieve_clinical_context_matches_transcript_keywords():
    """An eligible card can ground a note only when an internal caller supplies it."""
    entries = _schema_eligible_knowledge_document()["entries"]

    snippets = retrieve_clinical_context(
        "Patient uses naproxen with lisinopril.",
        knowledge_entries=entries,
    )

    assert snippets == [
        {
            "id": "renal-review",
            "title": "Renal review",
            "snippet": "Document renal-risk review.",
            "provenance": "docs/mock-documentation-source.md#renal-review",
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


def test_bundled_legacy_knowledge_is_ineligible() -> None:
    """The current legacy cards stay out of every clinician note by default."""
    assert load_clinical_knowledge() == []


def test_schema_eligible_knowledge_can_be_loaded_for_an_internal_probe(
    tmp_path: Path,
) -> None:
    """A reviewed v1 asset remains available for a separately approved experiment."""
    knowledge_path = tmp_path / "clinical_knowledge.json"
    knowledge_path.write_text(
        json.dumps(_schema_eligible_knowledge_document()), encoding="utf-8"
    )

    loaded_entries = load_clinical_knowledge(knowledge_path)

    assert [entry["id"] for entry in loaded_entries] == ["renal-review"]


def test_summary_generation_keeps_context_disabled_by_default(monkeypatch) -> None:
    """Pressing Generate Summary uses transcript evidence without automatic cards."""
    from api import summary_generation

    def _unexpected_context_retrieval(_transcript: str):
        """Fail if the default clinician path tries to retrieve a knowledge card."""
        raise AssertionError("clinical context must stay disabled by default")

    def _provider_free_draft(_session_id, _prompt, _source_units):
        """Return an empty valid note so the test never reaches a model provider."""
        return summary_generation.SessionSummaryV2Output(), {}

    monkeypatch.setattr(
        summary_generation, "retrieve_clinical_context", _unexpected_context_retrieval
    )
    monkeypatch.setattr(
        summary_generation, "_generate_validated_v2_draft", _provider_free_draft
    )

    generated_note = summary_generation.run_summary_generation(
        "m01-context-default-off", "[DOCTOR] Hello."
    )

    assert generated_note is not None


def test_summary_generation_can_enable_only_schema_eligible_context(
    tmp_path: Path, monkeypatch
) -> None:
    """An internal approved probe can add a reviewed card without a UI control."""
    from api import summary_generation

    knowledge_path = tmp_path / "clinical_knowledge.json"
    knowledge_path.write_text(
        json.dumps(_schema_eligible_knowledge_document()), encoding="utf-8"
    )
    captured_prompts: list[str] = []

    def _provider_free_draft(_session_id, prompt, _source_units):
        """Capture the prompt and return a valid note without provider generation."""
        captured_prompts.append(prompt)
        return summary_generation.SessionSummaryV2Output(), {}

    monkeypatch.setattr(
        clinical_context_module,
        "default_clinical_knowledge_path",
        lambda: knowledge_path,
    )
    monkeypatch.setattr(
        summary_generation, "_generate_validated_v2_draft", _provider_free_draft
    )

    generated_note = summary_generation.run_summary_generation(
        "m01-context-explicit-on",
        "[PATIENT] I use naproxen.",
        context_enabled=True,
    )

    assert generated_note is not None
    assert "Renal review" in captured_prompts[0]
    assert "Document renal-risk review." in captured_prompts[0]


def test_context_seam_preserves_the_frozen_system_prompt() -> None:
    """Default-off plumbing cannot silently change the note instructions."""
    from agents.summary_agent import MEDICAL_SUMMARY_PROMPT

    prompt_sha256 = hashlib.sha256(MEDICAL_SUMMARY_PROMPT.encode("utf-8")).hexdigest()

    assert prompt_sha256 == (
        "443c02024d7cb872ddc8434068952486e203f6874dbdeae97d59b3697c75f8ff"
    )


def test_clinical_context_helpers_do_not_import_gpu_stack():
    """Summary context helpers must stay off the NeMo GPU lane."""
    project_root = Path(__file__).resolve().parents[2]
    helper_sources = [
        project_root / "strands_agents" / "clinical_context.py",
        project_root / "strands_agents" / "medical_lexicon.py",
    ]

    for helper_source in helper_sources:
        source_text = helper_source.read_text(encoding="utf-8")
        assert "import torch" not in source_text
        assert "import nemo" not in source_text
