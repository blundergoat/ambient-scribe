"""Protect the one reviewed phrase used to improve a stopped visit transcript.

These CPU-only contracts keep baseline wording unchanged, reject unreviewed hints,
and prove the candidate changes no beam, confidence, timestamp, or live-ASR setting.
"""

from __future__ import annotations

import argparse
import importlib.util
import inspect
import sys
from contextlib import nullcontext
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

import post_visit_correction as correction_module
from nemo_confidence import disable_word_confidence_decoding

TEST_REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_SCRIPTS_DIR = TEST_REPO_ROOT / "scripts"
sys.path.insert(0, str(TEST_SCRIPTS_DIR))
SECOND_PASS_SPEC = importlib.util.spec_from_file_location(
    "m02_second_pass_asr",
    TEST_SCRIPTS_DIR / "second_pass_asr.py",
)
assert SECOND_PASS_SPEC is not None
assert SECOND_PASS_SPEC.loader is not None
SECOND_PASS_MODULE = importlib.util.module_from_spec(SECOND_PASS_SPEC)
sys.modules[SECOND_PASS_SPEC.name] = SECOND_PASS_MODULE
SECOND_PASS_SPEC.loader.exec_module(SECOND_PASS_MODULE)


class _Config(dict[str, Any]):
    """Provide the attribute access NeMo config gives the stopped-visit decoder."""

    def __getattr__(self, key: str) -> Any:
        """Read one decoder field; a missing value fails the contract visibly."""
        return self[key]

    def __setattr__(self, key: str, value: Any) -> None:
        """Write one decoder field so the candidate test mirrors OmegaConf."""
        self[key] = value


def _config_value(value: Any) -> Any:
    """Convert nested test mappings into attribute-addressable decoder config."""
    # Nested mappings represent the decoder sections a reviewer compares.
    if isinstance(value, dict):
        return _Config({key: _config_value(item) for key, item in value.items()})
    # Lists preserve phrase order while converting any nested test values.
    if isinstance(value, list):
        return [_config_value(item) for item in value]
    return value


def _plain_value(value: Any) -> Any:
    """Convert the fake decoder config into the evidence JSON shape."""
    # Config mappings become plain evidence objects for exact assertions.
    if isinstance(value, dict):
        return {key: _plain_value(item) for key, item in value.items()}
    # Phrase lists retain their single approved value in evidence order.
    if isinstance(value, list):
        return [_plain_value(item) for item in value]
    return value


@pytest.fixture(autouse=True)
def fake_omegaconf(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a CPU-only config shim; no test imports NeMo or touches the GPU."""
    fake_module = ModuleType("omegaconf")
    fake_module.OmegaConf = SimpleNamespace(
        create=_config_value,
        to_container=lambda value, resolve=True: _plain_value(value),
    )
    fake_module.open_dict = lambda _value: nullcontext()
    monkeypatch.setitem(sys.modules, "omegaconf", fake_module)


def _fake_unified_model(strategy: str = "greedy_batch") -> SimpleNamespace:
    """Build a no-GPU decoder config that represents the user's stopped visit."""
    decoding_config = _config_value(
        {
            "strategy": strategy,
            "greedy": {
                "max_symbols": 10,
                "use_cuda_graph_decoder": False,
            },
            "beam": {
                "beam_size": 2,
                "return_best_hypothesis": False,
            },
            "confidence_cfg": {
                "preserve_frame_confidence": False,
                "preserve_token_confidence": True,
                "preserve_word_confidence": True,
                "exclude_blank": True,
                "aggregation": "min",
                "method_cfg": {"name": "max_prob"},
            },
        }
    )
    model = SimpleNamespace(
        cfg=SimpleNamespace(decoding=decoding_config),
        changed_configs=[],
    )

    def remember_decoder(candidate_config: object) -> None:
        """Record the candidate the clinician would receive without constructing NeMo."""
        model.changed_configs.append(candidate_config)
        model.cfg.decoding = candidate_config

    model.change_decoding_strategy = remember_decoder
    return model


def test_baseline_phrase_is_inactive_by_default() -> None:
    """The normal stopped-visit path keeps its current decoder before promotion."""
    model = _fake_unified_model()

    correction_module._apply_post_visit_correction_phrase(model, None)

    assert model.changed_configs == []
    assert correction_module.DEFAULT_POST_VISIT_CORRECTION_PHRASE is None


def test_word_confidence_recovery_changes_only_the_aggregation_field() -> None:
    """The recovery preserves phrase, token-confidence, beam, and greedy settings."""
    model = _fake_unified_model()
    original_config = _plain_value(model.cfg.decoding)

    disable_word_confidence_decoding(model)

    changed_config = _plain_value(model.changed_configs[0])
    expected_config = {
        **original_config,
        "confidence_cfg": {
            **original_config["confidence_cfg"],
            "preserve_word_confidence": False,
        },
    }
    assert changed_config == expected_config


def test_candidate_adds_only_the_reviewed_native_phrase_profile() -> None:
    """The approved arm adds one phrase while keeping greedy, beam, and confidence values."""
    model = _fake_unified_model()
    original_config = _plain_value(model.cfg.decoding)

    correction_module._apply_post_visit_correction_phrase(
        model,
        correction_module.APPROVED_POST_VISIT_CORRECTION_PHRASE,
    )

    changed_config = _plain_value(model.changed_configs[0])
    assert changed_config["strategy"] == original_config["strategy"]
    assert changed_config["beam"] == original_config["beam"]
    assert changed_config["confidence_cfg"] == original_config["confidence_cfg"]
    assert changed_config["greedy"]["max_symbols"] == 10
    assert changed_config["greedy"]["use_cuda_graph_decoder"] is False
    assert changed_config["greedy"]["boosting_tree"] == {
        "key_phrases_list": ["brand new sector"]
    }
    assert changed_config["greedy"]["boosting_tree_alpha"] == 1.0


@pytest.mark.parametrize(
    "unreviewed_phrase",
    ["", "new section", "brand new sector\nmetformin", "sector, section"],
)
def test_unreviewed_or_multiple_phrases_fail_closed(unreviewed_phrase: str) -> None:
    """Raw garbles never influence the clinician's corrected transcript.

    Reviewed phrases may now be applied as a list, but only after each one is
    matched against the inventory. Text that smuggles several terms through a
    single string - newline- or comma-joined - is still one unlisted phrase and
    is refused, so the list form cannot be reached by string manipulation.
    """
    with pytest.raises(
        correction_module.PostVisitCorrectionError,
        match="reviewed inventory",
    ):
        correction_module._apply_post_visit_correction_phrase(
            _fake_unified_model(),
            unreviewed_phrase,
        )


def test_non_greedy_batch_decoder_fails_closed() -> None:
    """An unsupported checkpoint stops instead of receiving an improvised decoder change."""
    with pytest.raises(
        correction_module.PostVisitCorrectionError,
        match="greedy_batch",
    ):
        correction_module._apply_post_visit_correction_phrase(
            _fake_unified_model("beam"),
            correction_module.APPROVED_POST_VISIT_CORRECTION_PHRASE,
        )


def test_transcription_keeps_timestamp_and_confidence_order() -> None:
    """Phrase bias remains after confidence setup and before timestamped decoding."""
    transcribe_source = inspect.getsource(correction_module.transcribe_audio_with_nemo)
    decode_source = inspect.getsource(correction_module._transcribe_loaded_model_once)

    assert transcribe_source.index("enable_word_confidence_decoding") < (
        transcribe_source.index("_apply_post_visit_correction_phrase")
    )
    assert "_transcribe_with_loaded_model" in transcribe_source
    assert "timestamps=True" in decode_source


def _evaluator_options(
    tmp_path: Path,
    *,
    application_post_visit: bool,
    correction_phrase: str | None,
    dry_run: bool = False,
) -> argparse.Namespace:
    """Build one evaluator choice before a user spends a frozen decode slot."""
    return argparse.Namespace(
        application_post_visit=application_post_visit,
        correction_phrase=correction_phrase,
        effective_decoder_output=tmp_path / "effective-decoder.json",
        model=SECOND_PASS_MODULE.M02_UNIFIED_MODEL,
        dry_run=dry_run,
    )


def test_evaluator_accepts_explicit_baseline_and_candidate(tmp_path: Path) -> None:
    """Both frozen arms reach the same application decoder with one phrase difference."""
    baseline_options = _evaluator_options(
        tmp_path,
        application_post_visit=True,
        correction_phrase=None,
    )
    candidate_options = _evaluator_options(
        tmp_path,
        application_post_visit=True,
        correction_phrase="brand new sector",
    )

    assert (
        SECOND_PASS_MODULE.validate_application_post_visit_options(baseline_options)
        is None
    )
    assert (
        SECOND_PASS_MODULE.validate_application_post_visit_options(candidate_options)
        is None
    )


def test_evaluator_rejects_phrase_on_legacy_path(tmp_path: Path) -> None:
    """A phrase cannot be mislabeled as a stopped-visit result on the legacy probe."""
    options = _evaluator_options(
        tmp_path,
        application_post_visit=False,
        correction_phrase="brand new sector",
    )

    assert SECOND_PASS_MODULE.validate_application_post_visit_options(options) == (
        "--correction-phrase requires --application-post-visit"
    )


def test_evaluator_rejects_unreviewed_phrase(tmp_path: Path) -> None:
    """A raw mistaken word cannot become an implicit correction hint.

    The gate reads the reviewed inventory rather than one hardcoded phrase, so
    the refusal message names the inventory. `new section` is in neither.
    """
    options = _evaluator_options(
        tmp_path,
        application_post_visit=True,
        correction_phrase="new section",
    )

    assert SECOND_PASS_MODULE.validate_application_post_visit_options(options) == (
        "--correction-phrase is not in the reviewed phrase inventory"
    )


def test_evaluator_accepts_a_reviewed_inventory_phrase(tmp_path: Path) -> None:
    """The reviewed list must be usable through the evaluator, not only in tests."""
    options = _evaluator_options(
        tmp_path,
        application_post_visit=True,
        correction_phrase="loratadine",
    )

    assert SECOND_PASS_MODULE.validate_application_post_visit_options(options) is None


def test_evaluator_dry_run_never_requires_decoder_output(tmp_path: Path) -> None:
    """Source preflight remains CPU-only and does not claim an effective decoder."""
    options = _evaluator_options(
        tmp_path,
        application_post_visit=True,
        correction_phrase=None,
        dry_run=True,
    )
    options.effective_decoder_output = None

    assert SECOND_PASS_MODULE.validate_application_post_visit_options(options) is None


def test_evaluator_cli_accepts_every_reviewed_inventory_phrase() -> None:
    """A reviewed phrase must be reachable through the evaluator, not just in tests.

    The CLI gate previously hardcoded the single historical control phrase, so
    the reviewed inventory could not be exercised through the only
    application-shaped path.
    """
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import second_pass_asr

    import correction_phrase_inventory as inventory

    allowed = second_pass_asr.reviewed_correction_phrases()

    assert second_pass_asr.M02_APPROVED_CORRECTION_PHRASE in allowed
    for phrase in inventory.approved_phrases():
        assert phrase in allowed, f"{phrase} is reviewed but unreachable from the CLI"


def test_evaluator_cli_is_never_more_permissive_than_the_decoder() -> None:
    """The pre-check may refuse more than the decoder; it must never allow more."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import second_pass_asr

    import correction_phrase_inventory as inventory

    decoder_allows = set(inventory.approved_phrases())
    decoder_allows.add(correction_module.APPROVED_POST_VISIT_CORRECTION_PHRASE)

    assert second_pass_asr.reviewed_correction_phrases() <= decoder_allows


def test_evaluator_cli_falls_back_to_the_control_phrase_if_the_inventory_is_unreadable(
    monkeypatch,
) -> None:
    """A missing inventory must narrow what the CLI accepts, never widen it."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import second_pass_asr

    monkeypatch.setattr(
        second_pass_asr,
        "CORRECTION_PHRASE_INVENTORY_PATH",
        Path("/nonexistent/inventory.json"),
    )

    assert second_pass_asr.reviewed_correction_phrases() == {
        second_pass_asr.M02_APPROVED_CORRECTION_PHRASE
    }
