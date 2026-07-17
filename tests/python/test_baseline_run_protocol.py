"""Protect the frozen 0.5.0 no-intervention replay protocol.

These checks keep developer replays on the ten visits approved for quality work.
They prevent an empty or broad command from exposing extra consultations to evaluation.
They also pin the evidence clinicians rely on when comparing live and corrected text.
"""

import json
import re
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BASELINE_PROTOCOL_PATH = (
    REPOSITORY_ROOT / ".goat-flow/plans/0.5.0/BASELINE-RUN-PROTOCOL.md"
)
DEVELOPMENT_MANIFEST_PATH = (
    REPOSITORY_ROOT / "tests/fixtures/audio/development-corpus-0.5.0.json"
)


def test_protocol_uses_the_frozen_development_order() -> None:
    """Keep every replay on the same ten visits and in the same review order."""
    development_manifest = json.loads(
        DEVELOPMENT_MANIFEST_PATH.read_text(encoding="utf-8")
    )
    baseline_protocol = BASELINE_PROTOCOL_PATH.read_text(encoding="utf-8")
    ordered_development_stems = tuple(
        fixture["stem"] for fixture in development_manifest["fixtures"]
    )
    stem_array_match = re.search(
        r"development_stems=\(\n(?P<body>.*?)\n\)",
        baseline_protocol,
        flags=re.DOTALL,
    )

    # A missing array would let a user run a broad or incomplete consultation set.
    assert stem_array_match is not None
    protocol_stems = tuple(re.findall(r"'([^']+)'", stem_array_match.group("body")))

    assert protocol_stems == ordered_development_stems


def test_protocol_keeps_sealed_visits_out_of_development_commands() -> None:
    """Prevent a clinician's sealed comparison visits entering development replay."""
    development_manifest = json.loads(
        DEVELOPMENT_MANIFEST_PATH.read_text(encoding="utf-8")
    )
    baseline_protocol = BASELINE_PROTOCOL_PATH.read_text(encoding="utf-8")
    sealed_holdout_registry = development_manifest["sealed_holdouts"]
    sealed_stems = tuple(sealed_holdout_registry["primary"]) + tuple(
        sealed_holdout_registry["contingency"]
    )

    # Any sealed stem in this protocol would make its later release check untrustworthy.
    assert all(sealed_stem not in baseline_protocol for sealed_stem in sealed_stems)


def test_each_runner_receives_only_the_explicit_stem_array() -> None:
    """Require both user-visible transcript lanes to receive ten named visits."""
    baseline_protocol = BASELINE_PROTOCOL_PATH.read_text(encoding="utf-8")
    executable_runner_lines = re.findall(
        r"\./scripts/eval(?:-corrected)?-fixtures\.sh[^\n]*", baseline_protocol
    )

    assert executable_runner_lines == [
        './scripts/eval-fixtures.sh "${development_stems[@]}" \\',
        './scripts/eval-corrected-fixtures.sh "${development_stems[@]}" \\',
    ]


def test_protocol_pins_repetition_runtime_and_failure_evidence() -> None:
    """Keep baseline variance, browser pacing, and failed visits reviewable."""
    baseline_protocol = BASELINE_PROTOCOL_PATH.read_text(encoding="utf-8")
    required_contract_phrases = (
        "Exactly three repetitions per lane",
        "EVAL_PACE='1x'",
        "EVAL_CHUNK_MS='5000'",
        "EVAL_REQUIRE_STRUCTURED_LOGS='1'",
        "gpu-samples-10s.csv",
        "never selectively replaced",
        "do not rerun the failed fixture",
        "must never overlap",
    )

    # Every phrase closes a path that could make the clinician-facing comparison misleading.
    assert all(
        required_phrase in baseline_protocol
        for required_phrase in required_contract_phrases
    )
