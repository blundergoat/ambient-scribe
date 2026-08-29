"""Verify the offline evaluator before an operator spends GPU time.

These tests exercise frozen selection, source identity, application import,
failure retention, and write-once behavior with synthetic consultation data.
They never load NeMo, inspect sealed content, or change the Scribe workflow.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import wave
from contextlib import nullcontext
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

import post_visit_correction as correction_module

REPO_ROOT = Path(__file__).resolve().parents[2]
SECOND_PASS_SCRIPT = REPO_ROOT / "scripts" / "second_pass_asr.py"
UNIFIED_MODEL_NAME = "nvidia/parakeet-unified-en-0.6b"


def _load_module_from_path(module_name: str, module_path: Path) -> ModuleType:
    """Load one evaluator module without running an operator command.

    Args:
        module_name: Import identity; empty would not provide a reusable test module.
        module_path: Python source path; an absent file fails the focused test setup.

    Returns:
        Imported helper module; it never loads NeMo merely by being imported.
    """
    module_spec = importlib.util.spec_from_file_location(
        module_name,
        module_path,
    )
    assert module_spec is not None
    assert module_spec.loader is not None
    loaded_module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_spec.name] = loaded_module
    module_spec.loader.exec_module(loaded_module)
    return loaded_module


SECOND_PASS_PRODUCTION = _load_module_from_path(
    "second_pass_production",
    SECOND_PASS_SCRIPT.with_name("second_pass_production.py"),
)
SECOND_PASS = _load_module_from_path(
    "ambient_scribe_second_pass_test",
    SECOND_PASS_SCRIPT,
)


def _write_pcm_wav(audio_path: Path) -> None:
    """Create browser-format silence representing one stopped test consultation.

    Args:
        audio_path: New WAV destination; an absent parent is created for the test user.
    """
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(audio_path), "wb") as audio_file:
        audio_file.setnchannels(1)
        audio_file.setsampwidth(2)
        audio_file.setframerate(16_000)
        audio_file.writeframes(b"\0\0" * 320)


def _production_options(tmp_path: Path, *, dry_run: bool) -> argparse.Namespace:
    """Build one source-stable evaluator case without real model or fixture access.

    Args:
        tmp_path: Isolated test folder; an absent folder is supplied by pytest.
        dry_run: True must stop before importing application correction code.

    Returns:
        Complete production options; no required source or identity is null or empty.
    """
    fixture_stem = SECOND_PASS.EXPECTED_DEVELOPMENT_STEMS[0]
    fixture_source_dir = tmp_path / fixture_stem
    audio_path = fixture_source_dir / f"{fixture_stem}.wav"
    live_history_path = fixture_source_dir / "live-history.json"
    development_manifest_path = tmp_path / SECOND_PASS.DEVELOPMENT_MANIFEST_NAME
    _write_pcm_wav(audio_path)
    live_history_path.write_text(
        json.dumps(
            {
                "session_id": "synthetic-development-session",
                "segments": [
                    {
                        "segment_id": "seg-0001",
                        "speaker_id": "speaker_0",
                        "role": "DOCTOR",
                        "start": 0.0,
                        "end": 0.02,
                        "text": "hello there",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    development_manifest_path.write_text(
        '{"schema_version":"ambient-scribe-development-corpus/v1"}\n',
        encoding="utf-8",
    )
    return argparse.Namespace(
        production_shape=True,
        fixture_stem=fixture_stem,
        audio=audio_path,
        live_history=live_history_path,
        development_manifest=development_manifest_path,
        expected_audio_bytes=audio_path.stat().st_size,
        expected_audio_sha256=SECOND_PASS.file_sha256(audio_path),
        expected_live_history_bytes=live_history_path.stat().st_size,
        expected_live_history_sha256=SECOND_PASS.file_sha256(live_history_path),
        expected_manifest_bytes=development_manifest_path.stat().st_size,
        expected_manifest_sha256=SECOND_PASS.file_sha256(development_manifest_path),
        history_output=tmp_path / "history.json",
        metadata_output=tmp_path / "metadata.json",
        model="nvidia/parakeet-unified-en-0.6b",
        seconds=None,
        dry_run=dry_run,
    )


def _read_json(json_path: Path) -> dict[str, Any]:
    """Read one generated operator artifact for focused assertions.

    Args:
        json_path: Expected JSON file; an absent path means the evaluator failed its contract.

    Returns:
        Parsed object; an empty object remains an explicit valid test result.
    """
    return json.loads(json_path.read_text(encoding="utf-8"))


def _pin_synthetic_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
    *,
    model_name: str,
    checkpoint_path: Path,
    expected_bytes: int,
    expected_sha256: str,
) -> None:
    """Replace one frozen identity with a tiny local checkpoint for a focused test.

    Use this to prove the same guard a stopped consultation uses without loading
    NeMo or hashing a multi-gigabyte clinical model.

    Args:
        monkeypatch: Pytest patch manager; it restores the frozen identity after the test.
        model_name: Known correction model ID; empty would not exercise a pinned model.
        checkpoint_path: Local fake artifact; an absent file is used only by missing-cache tests.
        expected_bytes: Required fake artifact size; zero represents an intentionally empty file.
        expected_sha256: Required fake artifact hash; empty always represents drift.
    """
    monkeypatch.setitem(
        correction_module._PINNED_POST_VISIT_CHECKPOINTS,
        model_name,
        correction_module._PinnedPostVisitCheckpoint(
            repository_id=model_name,
            revision="frozen-test-revision",
            filename=checkpoint_path.name,
            expected_bytes=expected_bytes,
            expected_sha256=expected_sha256,
        ),
    )


def _install_fake_nemo_asr(
    monkeypatch: pytest.MonkeyPatch,
    model_api: SimpleNamespace,
) -> None:
    """Install a no-GPU NeMo module exposing the requested fake model API.

    Use this when testing which loader a stopped consultation selects; the
    fake never imports or constructs an actual speech model.

    Args:
        monkeypatch: Pytest patch manager; it removes fake modules after the test.
        model_api: Object with restore/from-pretrained methods; missing methods fail the test.
    """
    nemo_module = ModuleType("nemo")
    nemo_collections_module = ModuleType("nemo.collections")
    nemo_asr_module = ModuleType("nemo.collections.asr")
    nemo_asr_module.models = SimpleNamespace(ASRModel=model_api)
    nemo_collections_module.asr = nemo_asr_module
    nemo_module.collections = nemo_collections_module
    monkeypatch.setitem(sys.modules, "nemo", nemo_module)
    monkeypatch.setitem(sys.modules, "nemo.collections", nemo_collections_module)
    monkeypatch.setitem(sys.modules, "nemo.collections.asr", nemo_asr_module)


def test_pinned_checkpoint_uses_full_local_only_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stopped visit resolves Unified only from its exact approved cache identity."""
    import huggingface_hub

    checkpoint_path = tmp_path / "parakeet-unified-en-0.6b.nemo"
    checkpoint_content = b"synthetic unified checkpoint"
    checkpoint_path.write_bytes(checkpoint_content)
    _pin_synthetic_checkpoint(
        monkeypatch,
        model_name=UNIFIED_MODEL_NAME,
        checkpoint_path=checkpoint_path,
        expected_bytes=len(checkpoint_content),
        expected_sha256=hashlib.sha256(checkpoint_content).hexdigest(),
    )
    requested_checkpoint_identity: dict[str, Any] = {}

    def return_local_checkpoint(**download_options: Any) -> str:
        """Record the cache-only lookup and return the clinician test artifact."""
        requested_checkpoint_identity.update(download_options)
        return str(checkpoint_path)

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", return_local_checkpoint)

    verified_checkpoint_path = correction_module._verified_checkpoint_path(
        UNIFIED_MODEL_NAME
    )

    assert verified_checkpoint_path == checkpoint_path.resolve()
    assert requested_checkpoint_identity == {
        "repo_id": UNIFIED_MODEL_NAME,
        "revision": "frozen-test-revision",
        "filename": checkpoint_path.name,
        "cache_dir": str(correction_module.POST_VISIT_MODEL_CACHE_DIR),
        "local_files_only": True,
    }


def test_pinned_checkpoint_rejects_size_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A changed cache size blocks correction before the clinician's audio reaches NeMo."""
    import huggingface_hub

    checkpoint_path = tmp_path / "size-drift.nemo"
    checkpoint_content = b"changed checkpoint"
    checkpoint_path.write_bytes(checkpoint_content)
    _pin_synthetic_checkpoint(
        monkeypatch,
        model_name=UNIFIED_MODEL_NAME,
        checkpoint_path=checkpoint_path,
        expected_bytes=len(checkpoint_content) + 1,
        expected_sha256=hashlib.sha256(checkpoint_content).hexdigest(),
    )
    monkeypatch.setattr(
        huggingface_hub,
        "hf_hub_download",
        lambda **_download_options: str(checkpoint_path),
    )

    with pytest.raises(
        correction_module.PostVisitCorrectionError,
        match="size does not match",
    ) as correction_error:
        correction_module._verified_checkpoint_path(UNIFIED_MODEL_NAME)

    assert correction_error.value.reason_category == "model_load_failed"


def test_pinned_checkpoint_rejects_hash_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A changed cache hash blocks a plausible but unevaluated transcript model."""
    import huggingface_hub

    checkpoint_path = tmp_path / "hash-drift.nemo"
    checkpoint_content = b"another checkpoint"
    checkpoint_path.write_bytes(checkpoint_content)
    _pin_synthetic_checkpoint(
        monkeypatch,
        model_name=UNIFIED_MODEL_NAME,
        checkpoint_path=checkpoint_path,
        expected_bytes=len(checkpoint_content),
        expected_sha256="0" * 64,
    )
    monkeypatch.setattr(
        huggingface_hub,
        "hf_hub_download",
        lambda **_download_options: str(checkpoint_path),
    )

    with pytest.raises(
        correction_module.PostVisitCorrectionError,
        match="hash does not match",
    ) as correction_error:
        correction_module._verified_checkpoint_path(UNIFIED_MODEL_NAME)

    assert correction_error.value.reason_category == "model_load_failed"


def test_pinned_checkpoint_rejects_missing_local_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pruned local cache keeps the existing live transcript instead of downloading."""
    import huggingface_hub

    checkpoint_path = tmp_path / "missing.nemo"
    _pin_synthetic_checkpoint(
        monkeypatch,
        model_name=UNIFIED_MODEL_NAME,
        checkpoint_path=checkpoint_path,
        expected_bytes=1,
        expected_sha256="0" * 64,
    )

    def raise_missing_cache(**_download_options: Any) -> str:
        """Represent a pinned artifact removed before the user presses Stop."""
        raise FileNotFoundError(checkpoint_path)

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", raise_missing_cache)

    with pytest.raises(
        correction_module.PostVisitCorrectionError,
        match="unavailable from local cache",
    ) as correction_error:
        correction_module._verified_checkpoint_path(UNIFIED_MODEL_NAME)

    assert correction_error.value.reason_category == "model_load_failed"


def test_known_checkpoint_restores_verified_local_nemo_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unified uses local restore so the approved artifact creates the final transcript."""
    checkpoint_path = tmp_path / "verified-unified.nemo"
    checkpoint_path.write_bytes(b"verified")
    restored_paths: list[str] = []
    restored_model = SimpleNamespace(
        cfg=SimpleNamespace(validation_ds=None),
    )

    def open_test_model_config(unified_model_config: Any) -> nullcontext[None]:
        """Allow the synthetic Unified config to receive its missing loader value."""
        assert unified_model_config is restored_model.cfg
        return nullcontext()

    fake_omegaconf_module = ModuleType("omegaconf")
    fake_omegaconf_module.open_dict = open_test_model_config
    monkeypatch.setitem(sys.modules, "omegaconf", fake_omegaconf_module)

    def restore_verified_checkpoint(*, restore_path: str) -> object:
        """Record the exact local model path selected for the stopped visit."""
        restored_paths.append(restore_path)
        return restored_model

    model_api = SimpleNamespace(
        restore_from=restore_verified_checkpoint,
        from_pretrained=lambda **_options: pytest.fail(
            "a pinned checkpoint used floating repository loading"
        ),
    )
    _install_fake_nemo_asr(monkeypatch, model_api)
    monkeypatch.setattr(
        correction_module,
        "_verified_checkpoint_path",
        lambda _model_name: checkpoint_path,
    )

    loaded_model = correction_module._load_post_visit_asr_model(UNIFIED_MODEL_NAME)

    assert loaded_model is restored_model
    assert restored_paths == [str(checkpoint_path)]
    assert restored_model.cfg.validation_ds == {}


def test_unified_keeps_existing_validation_loader_config() -> None:
    """A complete Unified checkpoint keeps the loader behavior chosen by its author."""
    restored_model = SimpleNamespace(
        cfg=SimpleNamespace(
            validation_ds=SimpleNamespace(use_start_end_token=True),
        ),
    )

    returned_model = correction_module._prepare_loaded_post_visit_asr_model(
        restored_model,
        UNIFIED_MODEL_NAME,
    )

    assert returned_model is restored_model
    assert restored_model.cfg.validation_ds.use_start_end_token is True


def test_other_model_keeps_missing_validation_loader_config() -> None:
    """A non-Unified override keeps the loader config its checkpoint author chose."""
    restored_model = SimpleNamespace(cfg=SimpleNamespace(validation_ds=None))

    returned_model = correction_module._prepare_loaded_post_visit_asr_model(
        restored_model,
        "operator/private-post-visit-model",
    )

    assert returned_model is restored_model
    assert restored_model.cfg.validation_ds is None


def test_unknown_override_retains_existing_repository_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit operator override keeps its prior loader without changing the UI."""
    override_model_name = "operator/private-post-visit-model"
    requested_model_names: list[str] = []
    loaded_model = object()

    def load_operator_override(*, model_name: str) -> object:
        """Record the existing repository-ID path selected by an operator override."""
        requested_model_names.append(model_name)
        return loaded_model

    model_api = SimpleNamespace(
        restore_from=lambda **_options: pytest.fail(
            "an unknown operator override used the pinned restore path"
        ),
        from_pretrained=load_operator_override,
    )
    _install_fake_nemo_asr(monkeypatch, model_api)

    returned_model = correction_module._load_post_visit_asr_model(override_model_name)

    assert returned_model is loaded_model
    assert requested_model_names == [override_model_name]


def test_unified_is_default_for_manual_transcription_review() -> None:
    """A stopped visit uses Unified so the clinician can review its transcript."""
    assert correction_module.DEFAULT_POST_VISIT_ASR_MODEL == UNIFIED_MODEL_NAME


def test_production_corpus_requires_exact_explicit_order() -> None:
    """Missing, implicit, sealed, and reordered campaigns fail without source access."""
    exact_development_stems = list(SECOND_PASS.EXPECTED_DEVELOPMENT_STEMS)
    SECOND_PASS.validate_production_corpus_selection(exact_development_stems)

    with pytest.raises(ValueError, match="exactly ten"):
        SECOND_PASS.validate_production_corpus_selection([])
    with pytest.raises(ValueError, match="--all is forbidden"):
        SECOND_PASS.validate_production_corpus_selection(["--all"])
    with pytest.raises(ValueError, match="position 1"):
        SECOND_PASS.validate_production_corpus_selection(
            [exact_development_stems[1], exact_development_stems[0]]
            + exact_development_stems[2:]
        )
    with pytest.raises(ValueError, match="position 1"):
        SECOND_PASS.validate_production_corpus_selection(
            ["primock57-day2-consultation07-im-having-chest-discomfort"]
            + exact_development_stems[1:]
        )


def test_production_dry_run_writes_stable_empty_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dry run proves copied sources and never imports the application model path."""
    operator_options = _production_options(tmp_path, dry_run=True)
    source_hashes_before = {
        source_path: SECOND_PASS.file_sha256(source_path)
        for source_path in (
            operator_options.audio,
            operator_options.live_history,
            operator_options.development_manifest,
        )
    }

    def reject_unexpected_application_import(_module_name: str) -> ModuleType:
        """Fail if a no-model dry run tries to import correction or NeMo code."""
        raise AssertionError("dry run imported application correction code")

    monkeypatch.setattr(
        SECOND_PASS.production.importlib,
        "import_module",
        reject_unexpected_application_import,
    )

    assert SECOND_PASS.production.run_production_shape(operator_options) == 0
    history = _read_json(operator_options.history_output)
    metadata = _read_json(operator_options.metadata_output)
    source_hashes_after = {
        source_path: SECOND_PASS.file_sha256(source_path)
        for source_path in source_hashes_before
    }

    assert history["segments"] == []
    assert metadata["status"] == "dry_run"
    assert metadata["module_origin"] is None
    assert metadata["audio_unchanged"] is True
    assert metadata["live_history_unchanged"] is True
    assert metadata["development_manifest_unchanged"] is True
    assert source_hashes_after == source_hashes_before


def test_production_import_failure_retains_empty_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wrong container module root becomes one failed artifact pair, not a lost case."""
    operator_options = _production_options(tmp_path, dry_run=False)

    def raise_missing_application_module(_module_name: str) -> ModuleType:
        """Represent an evaluator launched without the container's `/app` module root."""
        raise ModuleNotFoundError("No module named 'post_visit_correction'")

    monkeypatch.setattr(
        SECOND_PASS.production.importlib,
        "import_module",
        raise_missing_application_module,
    )

    assert SECOND_PASS.production.run_production_shape(operator_options) == 1
    history = _read_json(operator_options.history_output)
    metadata = _read_json(operator_options.metadata_output)

    assert history["segments"] == []
    assert metadata["status"] == "failed"
    assert metadata["reason_category"] == "evaluator_failed"
    assert metadata["error_type"] == "ModuleNotFoundError"
    assert metadata["attempts"] == 0
    assert metadata["chunk_count"] == 0
    assert metadata["timing_state"] == "unavailable"
    assert metadata["confidence_state"] == "unavailable"


def test_production_missing_source_retains_empty_evidence(tmp_path: Path) -> None:
    """A missing copied WAV records a failed case without importing correction code."""
    operator_options = _production_options(tmp_path, dry_run=False)
    operator_options.audio.unlink()

    assert SECOND_PASS.production.run_production_shape(operator_options) == 1
    history = _read_json(operator_options.history_output)
    metadata = _read_json(operator_options.metadata_output)

    assert history["segments"] == []
    assert metadata["status"] == "failed"
    assert metadata["reason_category"] == "missing_file"
    assert metadata["module_origin"] is None
    assert metadata["audio_sha256_before"] is None
    assert metadata["attempts"] == 0


def test_production_success_records_application_rows_and_origin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful application correction keeps rows, provenance, and source stability."""
    operator_options = _production_options(tmp_path, dry_run=False)
    corrected_segments = [
        {
            "segment_id": "corrected-0001",
            "speaker_id": "speaker_0",
            "role": "DOCTOR",
            "start": 0.0,
            "end": 0.02,
            "text": "hello there",
            "confidence": 0.92,
            "source": "post_visit_correction",
            "source_model": operator_options.model,
        }
    ]
    correction_result = SimpleNamespace(
        segments=corrected_segments,
        model_name=operator_options.model,
        word_count=2,
        source="post_visit_correction",
        attempts=1,
        retried=False,
        chunk_count=1,
    )
    correction_module = SimpleNamespace(
        __file__="/app/post_visit_correction.py",
        run_post_visit_correction=lambda **_correction_inputs: correction_result,
    )
    monkeypatch.setattr(
        SECOND_PASS.production.importlib,
        "import_module",
        lambda _module_name: correction_module,
    )

    assert SECOND_PASS.production.run_production_shape(operator_options) == 0
    history = _read_json(operator_options.history_output)
    metadata = _read_json(operator_options.metadata_output)

    assert history["segments"] == corrected_segments
    assert metadata["status"] == "success"
    assert metadata["module_origin"] == "/app/post_visit_correction.py"
    assert metadata["attempts"] == 1
    assert metadata["chunk_count"] == 1
    assert metadata["timed_row_count"] == 1
    assert metadata["confidence_row_count"] == 1


def test_production_outputs_are_write_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A repeated case cannot overwrite either completed operator artifact."""
    operator_options = _production_options(tmp_path, dry_run=True)
    monkeypatch.setattr(
        SECOND_PASS.production.importlib,
        "import_module",
        lambda _module_name: pytest.fail("dry run imported application code"),
    )

    assert SECOND_PASS.production.run_production_shape(operator_options) == 0
    history_before = operator_options.history_output.read_bytes()
    metadata_before = operator_options.metadata_output.read_bytes()

    assert SECOND_PASS.production.run_production_shape(operator_options) == 2
    assert operator_options.history_output.read_bytes() == history_before
    assert operator_options.metadata_output.read_bytes() == metadata_before


def test_shell_runner_uses_real_container_module_root_and_failure_copy() -> None:
    """The campaign command uses `/app` and retains both outputs after any model exit."""
    runner_source = (REPO_ROOT / "scripts" / "eval-second-pass.sh").read_text(
        encoding="utf-8"
    )
    host_selection_python = runner_source.split(
        '"$PYTHON_BIN" - "$@" <<\'PY\'\n',
        maxsplit=1,
    )[1].split("\nPY", maxsplit=1)[0]

    assert "-e PYTHONPATH=/app nemo-agent" in runner_source
    assert "PYTHONPATH=/app/strands_agents" not in runner_source
    assert "--all is forbidden" in runner_source
    assert 'runpy.run_path("scripts/second_pass_production.py")' in runner_source
    assert "candidate_artifacts_exist" in runner_source
    assert 'docker compose cp "nemo-agent:${container_history}"' in runner_source
    assert 'docker compose cp "nemo-agent:${container_metadata}"' in runner_source
    compile(host_selection_python, "eval-second-pass-host-selection", "exec")


class TestCorrectionReadiness:
    """Pre-visit readiness for the post-visit correction model.

    The clinician is told before recording whether a stopped visit can produce the reviewed
    transcript, so they never finish a consultation and discover the promise could not be kept.
    """

    def _clear_probe_cache(self, correction_module):
        """Drop memoised loadability verdicts so each case probes from scratch."""
        correction_module._CHECKPOINT_LOAD_PROBE_CACHE.clear()

    def test_missing_checkpoint_reports_not_ready_without_leaking_paths(
        self, monkeypatch, tmp_path
    ):
        """An absent checkpoint blocks the visit and explains itself in clinician-safe words."""
        self._clear_probe_cache(correction_module)
        monkeypatch.setattr(correction_module, "POST_VISIT_MODEL_CACHE_DIR", tmp_path)

        is_ready, detail = correction_module.correction_readiness()

        assert is_ready is False
        assert detail
        assert "/" not in detail
        assert "Traceback" not in detail

    def test_unloadable_checkpoint_reports_not_ready(self, monkeypatch, tmp_path):
        """Correct bytes this runtime cannot instantiate must not read as ready.

        This is the regression that mattered: byte verification passed for weeks while the
        container's NeMo could not build the model, so every stopped visit fell back silently.
        """
        self._clear_probe_cache(correction_module)
        checkpoint = tmp_path / "checkpoint.nemo"
        checkpoint.write_bytes(b"not-a-real-nemo-archive")

        monkeypatch.setattr(
            correction_module,
            "_verified_checkpoint_path",
            lambda model_name: checkpoint,
        )

        is_ready, detail = correction_module.correction_readiness()

        assert is_ready is False
        assert "/" not in detail

    def test_loadable_checkpoint_reports_ready(self, monkeypatch, tmp_path):
        """A checkpoint this runtime can restore lets the clinician start recording."""
        self._clear_probe_cache(correction_module)
        checkpoint = tmp_path / "checkpoint.nemo"
        checkpoint.write_bytes(b"stand-in-for-a-restorable-archive")

        monkeypatch.setattr(
            correction_module,
            "_verified_checkpoint_path",
            lambda model_name: checkpoint,
        )
        monkeypatch.setattr(
            correction_module,
            "_restore_checkpoint_for_probe",
            lambda checkpoint_path: None,
        )

        is_ready, detail = correction_module.correction_readiness()

        assert (is_ready, detail) == (True, "")

    def test_repeated_readiness_calls_probe_the_checkpoint_once(
        self, monkeypatch, tmp_path
    ):
        """Every Start click asks for readiness, so the expensive restore runs once per checkpoint."""
        self._clear_probe_cache(correction_module)
        checkpoint = tmp_path / "checkpoint.nemo"
        checkpoint.write_bytes(b"stand-in-for-a-restorable-archive")
        restore_calls = []

        monkeypatch.setattr(
            correction_module,
            "_verified_checkpoint_path",
            lambda model_name: checkpoint,
        )
        monkeypatch.setattr(
            correction_module,
            "_restore_checkpoint_for_probe",
            lambda checkpoint_path: restore_calls.append(checkpoint_path),
        )

        correction_module.correction_readiness()
        correction_module.correction_readiness()

        assert len(restore_calls) == 1

    def test_replacing_the_checkpoint_reprobes(self, monkeypatch, tmp_path):
        """Restoring a working checkpoint clears the block without restarting the agent."""
        self._clear_probe_cache(correction_module)
        checkpoint = tmp_path / "checkpoint.nemo"
        checkpoint.write_bytes(b"first-archive")
        restore_calls = []

        monkeypatch.setattr(
            correction_module,
            "_verified_checkpoint_path",
            lambda model_name: checkpoint,
        )
        monkeypatch.setattr(
            correction_module,
            "_restore_checkpoint_for_probe",
            lambda checkpoint_path: restore_calls.append(checkpoint_path),
        )

        correction_module.correction_readiness()
        checkpoint.write_bytes(b"a-different-archive-entirely")
        correction_module.correction_readiness()

        assert len(restore_calls) == 2


class TestEnsurePinnedCheckpointAvailable:
    """Start-time provisioning of the correction checkpoint into the local cache.

    An operator whose data volume was replaced can restart local work without discovering the missing
    correction model only after a consultation has already been recorded.
    """

    def test_present_checkpoint_is_reused_without_downloading(
        self, monkeypatch, tmp_path
    ):
        """A warm cache costs a verification, never a multi-gigabyte re-download."""
        checkpoint = tmp_path / "checkpoint.nemo"
        checkpoint.write_bytes(b"already-here")
        downloads = []

        monkeypatch.setattr(
            correction_module, "_verified_checkpoint_path", lambda model_name: checkpoint
        )
        monkeypatch.setattr(
            correction_module,
            "_download_pinned_checkpoint",
            lambda pin: downloads.append(pin),
        )

        resolved = correction_module.ensure_pinned_checkpoint_available(
            allow_download=True
        )

        assert resolved == checkpoint
        assert downloads == []

    def test_absent_checkpoint_downloads_then_verifies_exactly(
        self, monkeypatch, tmp_path
    ):
        """An emptied cache is refilled from the module's own pin and re-checked before use."""
        checkpoint = tmp_path / "checkpoint.nemo"
        downloads = []
        verifications = []

        def _verify(model_name):
            verifications.append(model_name)
            # The first look finds nothing; the look after the download must still verify exactly.
            if len(verifications) == 1:
                raise correction_module.PostVisitCorrectionError(
                    "Pinned post-visit ASR checkpoint is unavailable from local cache.",
                    reason_category="model_load_failed",
                )
            return checkpoint

        monkeypatch.setattr(correction_module, "_verified_checkpoint_path", _verify)
        monkeypatch.setattr(
            correction_module,
            "_download_pinned_checkpoint",
            lambda pin: downloads.append(pin),
        )

        resolved = correction_module.ensure_pinned_checkpoint_available(
            allow_download=True
        )

        assert resolved == checkpoint
        assert len(downloads) == 1
        assert downloads[0].revision
        assert len(verifications) == 2

    def test_download_uses_the_module_pin_never_a_floating_revision(
        self, monkeypatch, tmp_path
    ):
        """The refill must land the evaluated artifact, not whatever the repository serves today."""
        downloads = []

        def _verify(model_name):
            if not downloads:
                raise correction_module.PostVisitCorrectionError(
                    "Pinned post-visit ASR checkpoint is unavailable from local cache.",
                    reason_category="model_load_failed",
                )
            return tmp_path / "checkpoint.nemo"

        monkeypatch.setattr(correction_module, "_verified_checkpoint_path", _verify)
        monkeypatch.setattr(
            correction_module,
            "_download_pinned_checkpoint",
            lambda pin: downloads.append(pin),
        )

        correction_module.ensure_pinned_checkpoint_available(allow_download=True)

        expected = correction_module._PINNED_POST_VISIT_CHECKPOINTS[
            correction_module.DEFAULT_POST_VISIT_ASR_MODEL
        ]
        assert downloads[0] == expected

    def test_missing_checkpoint_without_permission_refuses_to_download(
        self, monkeypatch, tmp_path
    ):
        """Ordinary correction never reaches the network; only start-time provisioning may refill."""
        downloads = []

        def _verify(model_name):
            raise correction_module.PostVisitCorrectionError(
                "Pinned post-visit ASR checkpoint is unavailable from local cache.",
                reason_category="model_load_failed",
            )

        monkeypatch.setattr(correction_module, "_verified_checkpoint_path", _verify)
        monkeypatch.setattr(
            correction_module,
            "_download_pinned_checkpoint",
            lambda pin: downloads.append(pin),
        )

        with pytest.raises(correction_module.PostVisitCorrectionError):
            correction_module.ensure_pinned_checkpoint_available(allow_download=False)

        assert downloads == []
