"""
Tests for agent creation, model provider selection, and medical prompts.
"""

import os
from unittest.mock import patch

import pytest

from agents.summary_agent import MEDICAL_SUMMARY_PROMPT
from agents.transcription_agent import (
    MEDICAL_ROLE_INSTRUCTION,
    MEDICAL_ROLE_PROMPT,
)


class TestRoleAgentModelProviderSelection:
    """Test _create_role_agent_model provider branching."""

    def test_unknown_provider_raises_valueerror(self):
        with patch.dict(os.environ, {"ROLE_AGENT_MODEL_PROVIDER": "gpt4"}):
            # Need to reimport to pick up env change
            import importlib
            import agents.transcription_agent as mod

            importlib.reload(mod)
            try:
                with pytest.raises(
                    ValueError, match="Unknown ROLE_AGENT_MODEL_PROVIDER"
                ):
                    mod._create_role_agent_model()
            finally:
                # Restore default
                importlib.reload(mod)

    def test_ollama_provider_creates_model(self):
        with patch.dict(
            os.environ,
            {
                "ROLE_AGENT_MODEL_PROVIDER": "ollama",
                "OLLAMA_HOST": "http://localhost:11434",
                "ROLE_AGENT_OLLAMA_MODEL": "qwen2.5:14b",
            },
        ):
            import importlib
            import agents.transcription_agent as mod

            importlib.reload(mod)
            try:
                model = mod._create_role_agent_model()
                assert model is not None
            finally:
                importlib.reload(mod)


class TestSummaryAgentModelProviderSelection:
    """Test _create_summary_model provider branching."""

    def test_unknown_provider_raises_valueerror(self):
        with patch.dict(os.environ, {"ROLE_AGENT_MODEL_PROVIDER": "openai"}):
            import importlib
            import agents.summary_agent as mod

            importlib.reload(mod)
            try:
                with pytest.raises(
                    ValueError, match="Unknown ROLE_AGENT_MODEL_PROVIDER"
                ):
                    mod._create_summary_model()
            finally:
                importlib.reload(mod)


class TestMedicalRoleInstruction:
    """Test the one instruction shown to the role agent during a consultation."""

    def test_medical_instruction(self):
        assert "DOCTOR/PATIENT" in MEDICAL_ROLE_INSTRUCTION


class TestPromptContent:
    """Verify prompt content for both agent types."""

    def test_medical_role_prompt_mentions_tool(self):
        """Medical role prompt should instruct the agent to call assign_roles."""
        assert "assign_roles" in MEDICAL_ROLE_PROMPT

    def test_medical_role_prompt_has_json_fallback(self):
        """Medical role prompt has the JSON fallback output format."""
        assert '"mapping"' in MEDICAL_ROLE_PROMPT
        assert '"confidence"' in MEDICAL_ROLE_PROMPT

    def test_summary_medical_has_soap_sections(self):
        for section in ("Subjective", "Objective", "Assessment", "Plan"):
            assert section in MEDICAL_SUMMARY_PROMPT
