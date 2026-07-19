#!/usr/bin/env python3
"""Evaluate the post-ASR medical correction fallback without loading NeMo.

Use this when a reviewer wants the before/after table for a lexicon edit. It
scores the checked-in correction rules against every reviewed pair and guard
sentence so visible transcript behavior can be reviewed on CPU. The full
governance gate (provenance, review identity, corpus sweep) is
scripts/clinical-data-audit.py; this evaluator proves runtime behavior only.
"""

from __future__ import annotations

import hashlib
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
ALLOWED_CATEGORIES = {
    "asr_variant",
    "abbreviation_acronym",
    "semantic_synonym",
    # Wording too ambiguous to map to any product; documented, never corrected.
    "ambiguous_product",
}


class LexiconPhrase(Protocol):
    """Minimal phrase shape loaded from the runtime medical lexicon.

    Use this when the evaluator checks reviewer coverage without importing the
    app package through PYTHONPATH. The clinician-facing field is the canonical
    term that appears in the transcript; variants include the canonical itself.
    """

    canonical: str
    variants: tuple[str, ...]


@dataclass(frozen=True)
class ReviewEntry:
    """One reviewer-approved ledger pair for visible transcript text.

    Use this to compare the exact ASR wording a user might see raw with the
    wording the clinician should see after the fallback. Disabled and rejected
    rows prove risky candidates stay inactive.

    Attributes:
        entry_id: Stable ledger row ID; empty fails validation.
        canonical: Reviewer-approved phrase; empty fails validation.
        variant: Exact ASR phrase this row authorizes; empty fails validation.
        status: `active` changes visible text; `disabled`/`rejected` must not.
        category: Review category; unknown values fail validation.
        raw_text: ASR text to score; empty means no behavior proof exists.
        expected_visible_text: Text the clinician should see after correction.
        hard_negatives: Sentences that must stay unchanged; empty fails validation.
        safety_rationale: Why the visible behavior is safe; empty fails validation.
    """

    entry_id: str
    canonical: str
    variant: str
    status: str
    category: str
    raw_text: str
    expected_visible_text: str
    hard_negatives: tuple[str, ...]
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
    """Load the v2 pair ledger used for CPU-only correction evaluation.

    Args:
        review_path: Ledger path; missing means no lexicon pair can be accepted.

    Returns:
        Ledger rows; empty means the evaluator has no evidence to score.

    Raises:
        ValueError: When the ledger shape cannot prove reviewer coverage.
        json.JSONDecodeError: When the ledger is not valid JSON.
    """
    payload = json.loads(review_path.read_text(encoding="utf-8"))
    entries = payload.get("entries", [])
    # Invalid ledger shape blocks acceptance because reviewer evidence is missing.
    if not isinstance(entries, list):
        raise ValueError("medical lexicon review entries must be a list")

    review_entries: list[ReviewEntry] = []
    # Every row becomes one scoreable positive/negative expectation.
    for entry in entries:
        # Malformed rows cannot prove safety for a visible transcript correction.
        if not isinstance(entry, dict):
            raise ValueError("medical lexicon review entry must be an object")

        hard_negatives = entry.get("hard_negatives", [])
        # A non-list guard field cannot prove unrelated wording stays unchanged.
        if not isinstance(hard_negatives, list):
            raise ValueError("medical lexicon review hard_negatives must be a list")

        review_entries.append(
            ReviewEntry(
                entry_id=str(entry.get("id", "")),
                canonical=str(entry.get("canonical", "")),
                variant=str(entry.get("variant", "")),
                status=str(entry.get("status", "")),
                category=str(entry.get("category", "")),
                raw_text=str(entry.get("raw_text", "")),
                expected_visible_text=str(entry.get("expected_visible_text", "")),
                hard_negatives=tuple(str(guard) for guard in hard_negatives),
                safety_rationale=str(entry.get("safety_rationale", "")),
            )
        )

    return review_entries


def validate_runtime_binding(review_path: Path, lexicon_path: Path) -> list[str]:
    """Confirm the ledger binds the exact runtime lexicon bytes.

    Args:
        review_path: Ledger path; missing binding means unreviewed runtime data.
        lexicon_path: Runtime lexicon path; missing means nothing is executable.

    Returns:
        Issue messages; empty means the reviewed and executable bytes match.
    """
    payload = json.loads(review_path.read_text(encoding="utf-8"))
    recorded_hash = str(payload.get("runtime_lexicon", {}).get("sha256", ""))
    actual_hash = hashlib.sha256(lexicon_path.read_bytes()).hexdigest()
    # A hash mismatch means the human reviewed different executable data.
    if recorded_hash != actual_hash:
        return [
            "runtime_lexicon sha256 mismatch: ledger does not bind "
            "the current medical_lexicon.txt"
        ]
    return []


def validate_review_entries(review_entries: list[ReviewEntry]) -> list[str]:
    """Return ledger issues that would make the eval misleading.

    Args:
        review_entries: Rows loaded from the ledger; empty means no active pair proof.

    Returns:
        Issue messages; empty means the table is structurally reviewable.
    """
    issues: list[str] = []
    # Each row must carry enough context for a clinician or developer review.
    for entry in review_entries:
        row_label = entry.entry_id or entry.canonical or "<missing>"
        # Empty fields mean the reviewer cannot tell what the user would see.
        if (
            entry.entry_id == ""
            or entry.canonical == ""
            or entry.variant == ""
            or entry.status == ""
            or entry.category == ""
            or entry.raw_text == ""
            or entry.expected_visible_text == ""
            or entry.hard_negatives == ()
            or entry.safety_rationale == ""
        ):
            issues.append(f"{row_label}: missing required review field")

        # Categories are intentionally narrow so synonyms cannot hide as ASR variants.
        if entry.category not in ALLOWED_CATEGORIES:
            issues.append(f"{row_label}: unknown category {entry.category!r}")

        # Rejected rows document why a candidate is unsafe without correcting anything;
        # only active/disabled/rejected statuses are accepted for visible corrections.
        if entry.status not in {"active", "disabled", "rejected"}:
            issues.append(f"{row_label}: unknown status {entry.status!r}")

        # A rejected mapping must never change what the clinician sees.
        if entry.status == "rejected" and entry.raw_text != entry.expected_visible_text:
            issues.append(f"{row_label}: rejected row must not rewrite wording")

        # Active semantic synonyms would rewrite the patient's wording into a diagnosis.
        if entry.status == "active" and entry.category == "semantic_synonym":
            issues.append(f"{row_label}: semantic synonym cannot be active")

        # Active rows must show exactly the reviewed variant and canonical.
        if entry.status == "active" and (
            entry.raw_text != entry.variant
            or entry.expected_visible_text != entry.canonical
        ):
            issues.append(f"{row_label}: active raw/visible text must equal variant/canonical")

    return issues


def _pair_key(canonical: str, variant: str) -> tuple[str, str]:
    """Return one case-insensitive identity for a canonical/variant pair.

    Args:
        canonical: Clinician-visible term; empty stays empty and fails coverage.
        variant: ASR wording; empty stays empty and fails coverage.

    Returns:
        Casefolded pair key; never null.
    """
    return (canonical.casefold(), variant.casefold())


def validate_lexicon_review_coverage(
    review_entries: list[ReviewEntry],
    phrases: tuple[LexiconPhrase, ...],
) -> list[str]:
    """Confirm every executable pair has exactly one active reviewer row.

    Args:
        review_entries: Ledger rows; empty means no visible correction is accepted.
        phrases: Runtime lexicon rows; empty means there are no active corrections.

    Returns:
        Issue messages; empty means executable pairs and active rows match.
    """
    active_review_pairs = {
        _pair_key(entry.canonical, entry.variant)
        for entry in review_entries
        if entry.status == "active"
    }
    runtime_pairs = {
        _pair_key(phrase.canonical, variant)
        # The loader includes the canonical among variants; only real rewrites need review.
        for phrase in phrases
        for variant in phrase.variants
        if variant.casefold() != phrase.canonical.casefold()
    }
    issues: list[str] = []

    missing_review_pairs = sorted(runtime_pairs - active_review_pairs)
    # A missing row means a clinician could see an unreviewed correction.
    if missing_review_pairs:
        issues.append(
            "active lexicon pairs missing review rows: "
            + ", ".join(f"{pair[0]}::{pair[1]}" for pair in missing_review_pairs)
        )

    extra_review_pairs = sorted(active_review_pairs - runtime_pairs)
    # An extra active row means the evidence table overstates shipped behavior.
    if extra_review_pairs:
        issues.append(
            "active review pairs missing from lexicon: "
            + ", ".join(f"{pair[0]}::{pair[1]}" for pair in extra_review_pairs)
        )

    return issues


def score_review_entries(
    review_entries: list[ReviewEntry],
    phrases: tuple[LexiconPhrase, ...],
) -> list[str]:
    """Score raw pair text and guard sentences against the active fallback.

    Args:
        review_entries: Ledger rows; empty means no expected terms can be scored.
        phrases: Runtime lexicon rows; empty means active corrections cannot pass.

    Returns:
        Issue messages; empty means positives and negatives matched the ledger.
    """
    issues: list[str] = []
    # Each row proves either an active correction or an inactive risky candidate.
    for entry in review_entries:
        corrected_text = MEDICAL_LEXICON.correct_medical_terms(entry.raw_text, phrases)

        # Active raw text should become the reviewed visible text; the runtime
        # keeps a capital first letter from the ASR text, so compare casefolded.
        if entry.status == "active" and (
            corrected_text.casefold() != entry.expected_visible_text.casefold()
        ):
            issues.append(
                f"{entry.entry_id}: raw {entry.raw_text!r} -> {corrected_text!r}, "
                f"expected {entry.expected_visible_text!r}"
            )

        # Disabled and rejected wording must remain exactly as the user said it.
        if entry.status in {"disabled", "rejected"} and corrected_text != entry.raw_text:
            issues.append(
                f"{entry.entry_id}: inactive raw {entry.raw_text!r} was rewritten "
                f"to {corrected_text!r}"
            )

        # Guard sentences must never be altered by any lexicon row.
        for guard_sentence in entry.hard_negatives:
            corrected_guard = MEDICAL_LEXICON.correct_medical_terms(
                guard_sentence, phrases
            )
            # A changed guard proves an unrelated-wording rewrite the reviewer forbade.
            if corrected_guard != guard_sentence:
                issues.append(f"{entry.entry_id}: guard changed to {corrected_guard!r}")

    return issues


def print_review_table(
    review_entries: list[ReviewEntry],
    phrases: tuple[LexiconPhrase, ...],
) -> None:
    """Print the before/after table for the plan and changelog.

    Args:
        review_entries: Ledger rows; empty prints only the table header.
        phrases: Runtime lexicon rows; empty leaves active rows visibly raw.
    """
    print("status   category              raw phrase                    visible phrase")
    # The compact table is stable enough to paste into the milestone evidence.
    for entry in review_entries:
        corrected_text = MEDICAL_LEXICON.correct_medical_terms(
            entry.raw_text,
            phrases,
        )
        print(
            f"{entry.status[:8]:8} "
            f"{entry.category[:21]:21} "
            f"{entry.raw_text[:29]:29} "
            f"{corrected_text}"
        )


def main() -> int:
    """Run the CPU-only medical fallback evaluator.

    Returns:
        Process exit code; zero means the checked-in lexicon matches the ledger.
    """
    review_entries = load_review_entries()
    phrases = MEDICAL_LEXICON.load_medical_lexicon(LEXICON_PATH)
    issues = (
        validate_runtime_binding(REVIEW_PATH, LEXICON_PATH)
        + validate_review_entries(review_entries)
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
