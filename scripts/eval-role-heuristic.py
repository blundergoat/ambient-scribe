#!/usr/bin/env python3
"""
Evaluate the GPU-free medical role heuristic against scenario fixtures.

Use this when changing role fallback behavior or demo scenarios. The script
feeds fixture segment events into `_heuristic_role_inference`, compares the
speaker-role mapping to `expectedEndState.roleMapping`, emits JSON lines that
`analyze-logs.py` can ingest, and prints a short accuracy report.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
ROLE_HEURISTICS_MODULE = REPO_ROOT / "strands_agents/api/role_heuristics.py"


def load_heuristic_role_inference() -> Callable[
    [list[dict[str, Any]], str], dict[str, Any] | None
]:
    """Load the role heuristic without importing the full FastAPI app.

    Use this for fixture evaluation when the user wants a no-GPU quality check
    after editing demo scenarios or fallback role wording.

    Returns:
        Callable heuristic used by the app fallback path.

    Raises:
        ImportError: When the source file cannot be imported or does not expose the expected callable.
    """
    spec = importlib.util.spec_from_file_location(
        "ambient_scribe_role_heuristics", ROLE_HEURISTICS_MODULE
    )
    # Missing loader means the local checkout is incomplete, so the eval would not test the app code.
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load role heuristic from {ROLE_HEURISTICS_MODULE}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    candidate = getattr(module, "heuristic_role_inference", None)
    # The eval must call the same fallback users hit after the role model cannot classify speakers.
    if not callable(candidate):
        raise ImportError("role_heuristics.py must expose heuristic_role_inference")
    return candidate


HEURISTIC_ROLE_INFERENCE = load_heuristic_role_inference()


def load_scenarios(path: Path) -> list[dict[str, Any]]:
    """Load scenario fixtures from the demo corpus.

    Args:
        path: JSON fixture path; empty or missing data means no scenarios can be evaluated.

    Returns:
        Scenario objects from the fixture; empty means the eval has no ground truth.

    Raises:
        ValueError: When the fixture shape is not a scenario list and the report would be misleading.
    """
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    scenarios = payload.get("scenarios", [])
    # Invalid fixture shape should fail loudly because the eval would be misleading.
    if not isinstance(scenarios, list):
        raise ValueError("scenarios must be a list")
    return [scenario for scenario in scenarios if isinstance(scenario, dict)]


def segment_events(scenario: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract transcript segments from one scenario.

    Args:
        scenario: Demo scenario; missing events means no transcript is available.

    Returns:
        Segment dictionaries for heuristic input; empty means the scenario cannot infer roles.
    """
    segments: list[dict[str, Any]] = []
    # Only segment events represent words the user would see in the transcript.
    for event in scenario.get("events", []):
        # Non-dict events are ignored so one malformed fixture row does not crash all evals.
        if not isinstance(event, dict):
            continue
        data = event.get("data", {})
        # Role updates and controls are expected but not part of heuristic input.
        if event.get("type") == "segment" and isinstance(data, dict):
            segments.append(dict(data))
    return segments


def evaluate_scenario(scenario: dict[str, Any]) -> dict[str, Any] | None:
    """Evaluate one scenario with non-empty role ground truth.

    Args:
        scenario: Demo scenario with expected end-state role mapping.

    Returns:
        JSON-ready eval result, or null when the scenario has no expected role mapping.
    """
    expected = scenario.get("expectedEndState", {}).get("roleMapping", {})
    # Empty mappings are non-role scenarios; include them in scenario tests, not accuracy.
    if not isinstance(expected, dict) or expected == {}:
        return None

    segments = segment_events(scenario)
    transcript = "\n".join(str(segment.get("text", "")) for segment in segments)
    result = HEURISTIC_ROLE_INFERENCE(segments, transcript) or {}
    actual = result.get("mapping", {})
    # A malformed heuristic result is counted as zero correct for visible roles.
    if not isinstance(actual, dict):
        actual = {}

    correct = sum(
        1 for speaker_id, role in expected.items() if actual.get(speaker_id) == role
    )
    total = len(expected)
    accuracy = correct / total if total else 0.0

    return {
        "event": "eval.role_heuristic",
        "scenario_id": scenario.get("id", "unknown"),
        "speakers": total,
        "correct": correct,
        "accuracy": round(accuracy, 4),
        "path": result.get("path", "heuristic"),
    }


def evaluate(scenarios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Evaluate all scenarios that contain role ground truth.

    Args:
        scenarios: Demo scenarios; empty means no eval rows are produced.

    Returns:
        Per-scenario eval rows for JSONL output and aggregate reporting.
    """
    results: list[dict[str, Any]] = []
    # Each scenario is independent so one role mismatch does not hide the rest.
    for scenario in scenarios:
        result = evaluate_scenario(scenario)
        # Scenarios with no expected mapping are not part of role accuracy.
        if result is not None:
            results.append(result)
    return results


def print_report(results: list[dict[str, Any]]) -> None:
    """Print a human-readable eval summary after JSON lines.

    Args:
        results: Per-scenario results; empty means no role-mapping fixtures were present.
    """
    total_speakers = sum(int(result["speakers"]) for result in results)
    correct = sum(int(result["correct"]) for result in results)
    accuracy = correct / total_speakers if total_speakers else 0.0
    print("")
    print("Role heuristic evaluation")
    print(f"  scenarios={len(results)} speakers={total_speakers} correct={correct}")
    print(f"  overall_accuracy={accuracy:.2%}")


def main(argv: list[str] | None = None) -> int:
    """Run the heuristic evaluation CLI.

    Args:
        argv: Optional args for tests; null reads the current process args.

    Returns:
        Exit status; zero means the eval completed without GPU or LLM access.
    """
    parser = argparse.ArgumentParser(
        description="Evaluate medical role heuristic fixtures."
    )
    parser.add_argument(
        "--scenarios",
        type=Path,
        default=REPO_ROOT / "tests/fixtures/scribe/scenarios.json",
        help="Scenario fixture JSON path",
    )
    args = parser.parse_args(argv)

    results = evaluate(load_scenarios(args.scenarios))
    # JSON lines are emitted first so analyze-logs.py can consume this output directly.
    for result in results:
        print(json.dumps(result, sort_keys=True))
    print_report(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
