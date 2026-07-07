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
                "ROLE_AGENT_OLLAMA_MODEL": "qwen3.5:9b",
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


class TestAgentCallbackHandling:
    """Verify role and summary agents do not stream model prose into Docker logs."""

    def test_role_agent_uses_null_callback_handler(self, monkeypatch):
        """Role inference should keep reasoning out of the clinician's log stream."""
        import agents.transcription_agent as mod

        captured_kwargs = {}

        class FakeAgent:
            """Capture constructor args without starting a real Bedrock or Ollama model."""

            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)

        monkeypatch.setattr(mod, "_create_role_agent_model", lambda: object())
        monkeypatch.setattr("strands.Agent", FakeAgent)

        mod.create_role_inference_agent()

        assert captured_kwargs["callback_handler"] is None

    def test_summary_agent_uses_null_callback_handler(self, monkeypatch):
        """Summary generation should return JSON without printing streamed prose."""
        import agents.summary_agent as mod

        captured_kwargs = {}

        class FakeAgent:
            """Capture constructor args without starting a real Bedrock or Ollama model."""

            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)

        monkeypatch.setattr(mod, "_create_summary_model", lambda: object())
        monkeypatch.setattr("strands.Agent", FakeAgent)

        mod.create_summary_agent()

        assert captured_kwargs["callback_handler"] is None

    def test_role_agent_factory_returns_fresh_agent(self, monkeypatch):
        """Each role request gets isolated SDK conversation history."""
        import agents.transcription_agent as mod

        class FakeAgent:
            """Minimal agent object used to prove factories are not singleton cached."""

            def __init__(self, **kwargs):
                self.kwargs = kwargs

        monkeypatch.setattr(mod, "_create_role_agent_model", lambda: object())
        monkeypatch.setattr("strands.Agent", FakeAgent)

        first_agent = mod.create_role_inference_agent()
        second_agent = mod.create_role_inference_agent()

        assert first_agent is not second_agent

    def test_summary_agent_factory_returns_fresh_agent(self, monkeypatch):
        """Each summary request gets isolated SDK conversation history."""
        import agents.summary_agent as mod

        class FakeAgent:
            """Minimal agent object used to prove factories are not singleton cached."""

            def __init__(self, **kwargs):
                self.kwargs = kwargs

        monkeypatch.setattr(mod, "_create_summary_model", lambda: object())
        monkeypatch.setattr("strands.Agent", FakeAgent)

        first_agent = mod.create_summary_agent()
        second_agent = mod.create_summary_agent()

        assert first_agent is not second_agent


class TestSummaryAgentModelProviderSelection:
    """Test _create_summary_model provider branching."""

    def test_unknown_provider_raises_valueerror(self):
        with patch.dict(os.environ, {"SUMMARY_AGENT_MODEL_PROVIDER": "openai"}):
            import importlib
            import agents.summary_agent as mod

            importlib.reload(mod)
            try:
                with pytest.raises(
                    ValueError, match="Unknown SUMMARY_AGENT_MODEL_PROVIDER"
                ):
                    mod._create_summary_model()
            finally:
                importlib.reload(mod)

    def test_summary_agent_uses_independent_model_env(self, monkeypatch):
        """Summary model settings can be tuned without changing role inference."""
        with patch.dict(
            os.environ,
            {
                "SUMMARY_AGENT_MODEL_PROVIDER": "bedrock",
                "SUMMARY_AGENT_MODEL_ID": "summary-model",
                "SUMMARY_AGENT_MAX_TOKENS": "4096",
                "AWS_DEFAULT_REGION": "ap-southeast-2",
            },
        ):
            import importlib
            import agents.summary_agent as mod

            captured_kwargs = {}

            class FakeBedrockModel:
                """Capture Bedrock settings without contacting AWS."""

                def __init__(self, **kwargs):
                    captured_kwargs.update(kwargs)

            importlib.reload(mod)
            monkeypatch.setattr("strands.models.bedrock.BedrockModel", FakeBedrockModel)
            try:
                mod._create_summary_model()
            finally:
                importlib.reload(mod)

        assert captured_kwargs["model_id"] == "summary-model"
        assert captured_kwargs["max_tokens"] == 4096


class TestProviderParity:
    """Mock provider paths and assert the browser-facing agent contracts match."""

    @pytest.mark.parametrize("provider", ["bedrock", "ollama"])
    def test_role_agent_provider_paths_share_contract(self, monkeypatch, provider):
        """Role agent shape stays identical across Bedrock and CPU Ollama."""
        with patch.dict(os.environ, {"ROLE_AGENT_MODEL_PROVIDER": provider}):
            import importlib
            import agents.transcription_agent as mod

            captured_kwargs = {}

            class FakeAgent:
                """Capture agent contract without contacting a provider."""

                def __init__(self, **kwargs):
                    captured_kwargs.update(kwargs)

            importlib.reload(mod)
            monkeypatch.setattr(mod, "_create_role_agent_model", lambda: object())
            monkeypatch.setattr("strands.Agent", FakeAgent)
            try:
                mod.create_role_inference_agent()
            finally:
                importlib.reload(mod)

        assert captured_kwargs["callback_handler"] is None
        assert captured_kwargs["name"] == "role-inference"
        assert len(captured_kwargs["tools"]) == 1

    @pytest.mark.parametrize("provider", ["bedrock", "ollama"])
    def test_summary_agent_provider_paths_share_contract(self, monkeypatch, provider):
        """Summary agent shape stays identical across Bedrock and CPU Ollama."""
        with patch.dict(os.environ, {"SUMMARY_AGENT_MODEL_PROVIDER": provider}):
            import importlib
            import agents.summary_agent as mod

            captured_kwargs = {}

            class FakeAgent:
                """Capture agent contract without contacting a provider."""

                def __init__(self, **kwargs):
                    captured_kwargs.update(kwargs)

            importlib.reload(mod)
            monkeypatch.setattr(mod, "_create_summary_model", lambda: object())
            monkeypatch.setattr("strands.Agent", FakeAgent)
            try:
                mod.create_summary_agent()
            finally:
                importlib.reload(mod)

        assert captured_kwargs["callback_handler"] is None
        assert captured_kwargs["name"] == "summary"
        assert captured_kwargs["tools"] == []


class TestMedicalRoleInstruction:
    """Test the one instruction shown to the role agent during a consultation."""

    def test_medical_instruction(self):
        assert "DOCTOR/PATIENT" in MEDICAL_ROLE_INSTRUCTION


class TestPromptContent:
    """Verify prompt content for both agent types."""

    def test_medical_role_prompt_mentions_tool(self):
        """Medical role prompt should instruct the agent to call assign_roles."""
        assert "assign_roles" in MEDICAL_ROLE_PROMPT

    def test_medical_role_prompt_requires_tool_only_output(self):
        """Medical role prompt keeps role output inside the tool call."""
        assert "Do not answer in prose or JSON outside the tool call" in MEDICAL_ROLE_PROMPT
        assert "valid JSON only" not in MEDICAL_ROLE_PROMPT

    def test_medical_role_prompt_does_not_echo_segments_to_tool(self):
        """Role prompt keeps transcript rows out of the model-to-tool payload."""
        assert "segments: JSON string" not in MEDICAL_ROLE_PROMPT
        assert "attributed_segments" not in MEDICAL_ROLE_PROMPT
        assert "2 sentences or fewer" in MEDICAL_ROLE_PROMPT

    def test_medical_role_prompt_uses_rendered_roles_only(self):
        """Role prompt only asks for roles the browser renders today."""
        assert "NURSE" not in MEDICAL_ROLE_PROMPT
        assert "FAMILY_MEMBER" not in MEDICAL_ROLE_PROMPT
        assert "confidence:" in MEDICAL_ROLE_PROMPT

    def test_summary_medical_has_soap_sections(self):
        for section in ("Subjective", "Objective", "Assessment", "Plan"):
            assert section in MEDICAL_SUMMARY_PROMPT
