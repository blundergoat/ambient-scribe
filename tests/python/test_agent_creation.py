"""
Tests for agent creation, model provider selection, and prompt fallbacks.
"""

import os
from unittest.mock import patch

import pytest

from agents.summary_agent import SUMMARY_PROMPTS, _create_summary_model, create_summary_agent
from agents.transcription_agent import (
    ROLE_PROMPTS,
    _create_role_agent_model,
    create_role_inference_agent,
    get_role_instruction,
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
                with pytest.raises(ValueError, match="Unknown ROLE_AGENT_MODEL_PROVIDER"):
                    mod._create_role_agent_model()
            finally:
                # Restore default
                importlib.reload(mod)

    def test_ollama_provider_creates_model(self):
        with patch.dict(os.environ, {
            "ROLE_AGENT_MODEL_PROVIDER": "ollama",
            "OLLAMA_HOST": "http://localhost:11434",
            "ROLE_AGENT_OLLAMA_MODEL": "qwen2.5:14b",
        }):
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
                with pytest.raises(ValueError, match="Unknown ROLE_AGENT_MODEL_PROVIDER"):
                    mod._create_summary_model()
            finally:
                importlib.reload(mod)


class TestGetRoleInstruction:
    """Test mode instruction lookup."""

    def test_medical_instruction(self):
        assert "DOCTOR/PATIENT" in get_role_instruction("medical")

    def test_meeting_instruction(self):
        assert "ORGANISER/PARTICIPANT" in get_role_instruction("meeting")

    def test_interview_instruction(self):
        assert "INTERVIEWER/CANDIDATE" in get_role_instruction("interview")

    def test_tv_instruction(self):
        assert "HOST/GUEST" in get_role_instruction("tv")

    def test_lecture_instruction(self):
        assert "LECTURER/STUDENT" in get_role_instruction("lecture")

    def test_general_instruction(self):
        assert "SPEAKER_A/SPEAKER_B" in get_role_instruction("general")

    def test_unknown_mode_falls_back_to_general(self):
        result = get_role_instruction("podcast")
        assert "SPEAKER_A/SPEAKER_B" in result

    def test_empty_mode_falls_back_to_general(self):
        result = get_role_instruction("")
        assert "SPEAKER_A/SPEAKER_B" in result


class TestPromptContent:
    """Verify prompt content for both agent types."""

    def test_all_role_prompts_mention_tool(self):
        """Every role prompt should instruct the agent to call assign_roles."""
        for mode, prompt in ROLE_PROMPTS.items():
            assert "assign_roles" in prompt, f"{mode} prompt missing assign_roles instruction"

    def test_all_role_prompts_have_json_fallback(self):
        """Every role prompt has the JSON fallback output format."""
        for mode, prompt in ROLE_PROMPTS.items():
            assert '"mapping"' in prompt, f"{mode} prompt missing mapping in output format"
            assert '"confidence"' in prompt, f"{mode} prompt missing confidence in output format"

    def test_summary_prompts_cover_all_modes(self):
        expected_modes = {"medical", "meeting", "interview", "tv", "lecture", "general"}
        assert set(SUMMARY_PROMPTS.keys()) == expected_modes

    def test_role_prompts_cover_all_modes(self):
        expected_modes = {"medical", "meeting", "interview", "tv", "lecture", "general"}
        assert set(ROLE_PROMPTS.keys()) == expected_modes

    def test_summary_medical_has_soap_sections(self):
        prompt = SUMMARY_PROMPTS["medical"]
        for section in ("Subjective", "Objective", "Assessment", "Plan"):
            assert section in prompt

    def test_summary_meeting_has_action_items(self):
        prompt = SUMMARY_PROMPTS["meeting"]
        assert "Action Items" in prompt
        assert "Decisions" in prompt

    def test_summary_lecture_has_learning_objectives(self):
        prompt = SUMMARY_PROMPTS["lecture"]
        assert "Learning Objectives" in prompt
        assert "Key Concepts" in prompt

    def test_summary_tv_has_key_moments(self):
        prompt = SUMMARY_PROMPTS["tv"]
        assert "Key Moments" in prompt
