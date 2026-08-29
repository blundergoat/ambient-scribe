#!/usr/bin/env python3
"""
CLI report for corrected source-chip role review.

Run this against corrected transcript JSON files or a corrected fixture run
directory. It prints source-chip rows whose Doctor/Patient label contradicts
plain text cues, helping developers decide what to fix next without touching
the user's transcript, summary payload, or NeMo runtime.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

ROOT_DIR = Path(__file__).resolve().parents[1]
SCORE_MODULE_PATH = ROOT_DIR / "strands_agents" / "corrected_source_chip_score.py"


def load_score_module() -> ModuleType:
    """Load the scorer helper from this checkout without changing import paths.

    Returns:
        Imported scorer module used by the CLI report.

    Raises:
        RuntimeError: When the helper file cannot be loaded for local QA.
    """
    spec = importlib.util.spec_from_file_location(
        "corrected_source_chip_score_cli",
        SCORE_MODULE_PATH,
    )

    # Missing specs mean the checkout is incomplete or the script moved.
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load scorer helper at {SCORE_MODULE_PATH}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    """Read the developer's selected artifacts and report options.

    Returns:
        Parsed command-line options for the local QA report.
    """
    parser = argparse.ArgumentParser(
        description="Score corrected transcript source chips for role contradictions.",
    )
    parser.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help="Corrected transcript JSON files or directories containing them.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of the text report.",
    )
    parser.add_argument(
        "--fail-on-findings",
        action="store_true",
        help="Exit 1 when any source-chip review finding is present.",
    )
    return parser.parse_args()


def main() -> int:
    """Score selected corrected artifacts and print a local QA report.

    Returns:
        Process exit code; `0` means the report ran, `1` means opted-in
        finding failure, and `2` means no artifacts were found.
    """
    args = parse_args()
    score_module = load_score_module()
    artifact_paths = score_module.find_corrected_transcript_artifacts(args.paths)

    # Empty discovery usually means the developer pointed at the wrong run directory.
    if artifact_paths == []:
        print("error: no corrected transcript artifacts found", file=sys.stderr)
        return 2

    scores = [score_module.score_corrected_artifact(path) for path in artifact_paths]

    # JSON output is useful when another fixture script wants to consume the findings.
    if args.json:
        print(json.dumps([score.to_dict() for score in scores], indent=2))
    else:
        print(score_module.build_text_report(scores))

    total_findings = sum(score.finding_count for score in scores)
    # CI-style callers can opt into failing when review candidates are present.
    if args.fail_on_findings and total_findings > 0:
        return 1

    return 0


# Direct execution is the only supported CLI entry point.
if __name__ == "__main__":
    raise SystemExit(main())
