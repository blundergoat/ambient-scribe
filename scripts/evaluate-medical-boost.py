#!/usr/bin/env python3
"""Evaluate the post-ASR medical correction fallback without loading NeMo.

Use this when a reviewer wants the before/after table for M14 or a future
lexicon edit. It runs the checked-in correction rules against curated positive
and negative text so the visible transcript behavior can be reviewed on CPU.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Protocol

REPO_ROOT = Path(__file__).resolve().parents[1]
MEDICAL_LEXICON_MODULE_PATH = REPO_ROOT / "strands_agents" / "medical_lexicon.py"
LEXICON_PATH = REPO_ROOT / "strands_agents" / "data" / "medical_lexicon.txt"
REVIEW_PATH = REPO_ROOT / "strands_agents" / "data" / "medical_lexicon_review.json"
ALLOWED_CATEGORIES = {"asr_variant", "abbreviation_acronym", "semantic_synonym"}


class LexiconPhrase(Protocol):
    """Minimal phrase shape loaded from the runtime medical lexicon.

    Use this when the evaluator checks reviewer coverage without importing the
    app package through PYTHONPATH. The clinician-facing field is the canonical
    term that appears in the transcript.
    """

    canonical: str


@dataclass(frozen=True)
class ReviewEntry:
    """One reviewer-authored lexicon expectation for visible transcript text.

    Use this to compare the raw phrase a user might hear from ASR with the
    phrase the clinician should see after the fallback. Disabled entries prove
    risky prior behavior stays inactive.

    Attributes:
        canonical: Reviewer-approved phrase; empty fails validation.
        status: `active` changes visible text; `disabled` proves a risky row is inactive.
        category: Review category; unknown values fail validation.
        raw_phrase: ASR text to score; empty means no positive proof exists.
        expected_visible_phrase: Text the clinician should see after correction.
        false_positive_guard: Sentence that must stay unchanged for user safety.
        provenance: Source or reviewer note; empty means the row lacks evidence.
        safety_rationale: Why the visible correction is safe for review use.
    """

    canonical: str
    status: str
    category: str
    raw_phrase: str
    expected_visible_phrase: str
    false_positive_guard: str
    provenance: str
    safety_rationale: str


def load_medical_lexicon_module() -> ModuleType:
    """Load the flat medical lexicon helper without requiring PYTHONPATH.

    Returns:
        Loaded module; missing loader means the evaluator cannot score corrections.

    Raises:
        RuntimeError: When the local helper cannot be imported from its checked-in path.
    """
    spec = importlib.util.spec_from_file_location(
        "ambient_scribe_medical_lexicon",
        MEDICAL_LEXICON_MODULE_PATH,
    )
    # A missing spec means the reviewer cannot run the CPU-only proof.
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load medical_lexicon.py")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MEDICAL_LEXICON = load_medical_lexicon_module()


def load_review_entries(review_path: Path = REVIEW_PATH) -> list[ReviewEntry]:
    """Load the reviewer table used for CPU-only correction evaluation.

    Args:
        review_path: JSON sidecar path; missing means no lexicon row can be accepted.

    Returns:
        Review rows; empty means the evaluator has no evidence to score.

    Raises:
        ValueError: When the sidecar shape cannot prove reviewer coverage.
        json.JSONDecodeError: When the sidecar is not valid JSON.
    """
    payload = json.loads(review_path.read_text(encoding="utf-8"))
    entries = payload.get("entries", [])
    # Invalid sidecar shape blocks acceptance because reviewer evidence is missing.
    if not isinstance(entries, list):
        raise ValueError("medical lexicon review entries must be a list")

    review_entries: list[ReviewEntry] = []
    # Every row becomes one scoreable positive/negative expectation.
    for entry in entries:
        # Malformed rows cannot prove safety for a visible transcript correction.
        if not isinstance(entry, dict):
            raise ValueError("medical lexicon review entry must be an object")

        review_entries.append(
            ReviewEntry(
                canonical=str(entry.get("canonical", "")),
                status=str(entry.get("status", "")),
                category=str(entry.get("category", "")),
                raw_phrase=str(entry.get("raw_phrase", "")),
                expected_visible_phrase=str(entry.get("expected_visible_phrase", "")),
                false_positive_guard=str(entry.get("false_positive_guard", "")),
                provenance=str(entry.get("provenance", "")),
                safety_rationale=str(entry.get("safety_rationale", "")),
            )
        )

    return review_entries


def validate_review_entries(review_entries: list[ReviewEntry]) -> list[str]:
    """Return reviewer-table issues that would make the eval misleading.

    Args:
        review_entries: Rows loaded from the sidecar; empty means no active lexicon proof.

    Returns:
        Issue messages; empty means the table is structurally reviewable.
    """
    issues: list[str] = []
    # Each row must carry enough context for a clinician or developer review.
    for entry in review_entries:
        # Empty fields mean the reviewer cannot tell what the user would see.
        if (
            entry.canonical == ""
            or entry.status == ""
            or entry.category == ""
            or entry.raw_phrase == ""
            or entry.expected_visible_phrase == ""
            or entry.false_positive_guard == ""
            or entry.provenance == ""
            or entry.safety_rationale == ""
        ):
            issues.append(f"{entry.canonical or '<missing>'}: missing required review field")

        # Categories are intentionally narrow so synonyms cannot hide as ASR variants.
        if entry.category not in ALLOWED_CATEGORIES:
            issues.append(f"{entry.canonical}: unknown category {entry.category!r}")

        # Only explicit active/disabled statuses are accepted for visible corrections.
        if entry.status not in {"active", "disabled"}:
            issues.append(f"{entry.canonical}: unknown status {entry.status!r}")

        # Active semantic synonyms would rewrite the patient's wording into a diagnosis.
        if entry.status == "active" and entry.category == "semantic_synonym":
            issues.append(f"{entry.canonical}: semantic synonym cannot be active")

    return issues


def validate_lexicon_review_coverage(
    review_entries: list[ReviewEntry],
    phrases: tuple[LexiconPhrase, ...],
) -> list[str]:
    """Confirm every active transcript correction has reviewer evidence.

    Args:
        review_entries: Reviewer rows; empty means no visible correction is accepted.
        phrases: Runtime lexicon rows; empty means there are no active corrections.

    Returns:
        Issue messages; empty means active rows and reviewer rows match.
    """
    active_review_canonicals = {
        entry.canonical for entry in review_entries if entry.status == "active"
    }
    active_lexicon_canonicals = {phrase.canonical for phrase in phrases}
    issues: list[str] = []

    missing_review_rows = sorted(active_lexicon_canonicals - active_review_canonicals)
    # A missing review row means a clinician could see an unreviewed correction.
    if missing_review_rows:
        issues.append(
            "active lexicon rows missing review entries: "
            + ", ".join(missing_review_rows)
        )

    extra_review_rows = sorted(active_review_canonicals - active_lexicon_canonicals)
    # An extra active review row means the evidence table overstates shipped behavior.
    if extra_review_rows:
        issues.append(
            "active review entries missing from lexicon: "
            + ", ".join(extra_review_rows)
        )

    return issues


def score_review_entries(
    review_entries: list[ReviewEntry],
    phrases: tuple[LexiconPhrase, ...],
) -> list[str]:
    """Score raw phrases and guard text against the active fallback.

    Args:
        review_entries: Reviewer rows; empty means no expected terms can be scored.
        phrases: Runtime lexicon rows; empty means active corrections cannot pass.

    Returns:
        Issue messages; empty means positives and negatives matched the table.
    """
    issues: list[str] = []
    # Each row proves either an active correction or a disabled risky correction.
    for entry in review_entries:
        corrected_phrase = MEDICAL_LEXICON.correct_medical_terms(entry.raw_phrase, phrases)
        corrected_guard = MEDICAL_LEXICON.correct_medical_terms(
            entry.false_positive_guard,
            phrases,
        )

        # The raw phrase should become exactly what the reviewer expects to see.
        if corrected_phrase != entry.expected_visible_phrase:
            issues.append(
                f"{entry.canonical}: raw {entry.raw_phrase!r} -> {corrected_phrase!r}, "
                f"expected {entry.expected_visible_phrase!r}"
            )

        # The guard sentence should not be altered unless it deliberately contains the raw phrase.
        if corrected_guard != entry.false_positive_guard:
            issues.append(
                f"{entry.canonical}: guard changed to {corrected_guard!r}"
            )

    return issues


def print_review_table(
    review_entries: list[ReviewEntry],
    phrases: tuple[LexiconPhrase, ...],
) -> None:
    """Print the M14 before/after table for the plan and changelog.

    Args:
        review_entries: Reviewer rows; empty prints only the table header.
        phrases: Runtime lexicon rows; empty leaves active rows visibly raw.
    """
    print("status   category              raw phrase                    visible phrase")
    # The compact table is stable enough to paste into the milestone evidence.
    for entry in review_entries:
        corrected_phrase = MEDICAL_LEXICON.correct_medical_terms(
            entry.raw_phrase,
            phrases,
        )
        print(
            f"{entry.status[:8]:8} "
            f"{entry.category[:21]:21} "
            f"{entry.raw_phrase[:29]:29} "
            f"{corrected_phrase}"
        )


def main() -> int:
    """Run the CPU-only medical fallback evaluator.

    Returns:
        Process exit code; zero means the checked-in lexicon matches the review table.
    """
    review_entries = load_review_entries()
    phrases = MEDICAL_LEXICON.load_medical_lexicon(LEXICON_PATH)
    issues = (
        validate_review_entries(review_entries)
        + validate_lexicon_review_coverage(review_entries, phrases)
        + score_review_entries(review_entries, phrases)
    )
    print_review_table(review_entries, phrases)

    # Any issue means a lexicon edit needs review before users see it.
    if issues:
        print("\nissues:", file=sys.stderr)
        # Each issue names the row a reviewer needs to fix.
        for issue in issues:
            print(f"- {issue}", file=sys.stderr)
        return 1

    return 0


# Direct execution gives reviewers a CPU-only before/after proof table.
if __name__ == "__main__":
    raise SystemExit(main())
