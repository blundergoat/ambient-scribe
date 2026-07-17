"""Share frozen schema helpers across the clinical-data audit rules.

Use these pure helpers when cards and rewrite pairs need the same validation.
They own stable finding identity, text normalization, review identity, and privacy.
They accept in-memory data only and cannot read encounters or change user output.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

AUDIT_SCHEMA_VERSION = "ambient-scribe-clinical-data-audit/v1"
ASSET_CONTRACT_VERSION = "ambient-scribe-clinical-data-contract/v1"
KNOWLEDGE_SCHEMA_VERSION = "ambient-scribe-clinical-knowledge/v1"
LEXICON_SCHEMA_VERSION = "ambient-scribe-medical-lexicon/v1"
REVIEW_SCHEMA_VERSION = "ambient-scribe-medical-lexicon-review/v2"
NORMALIZED_ID_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
ASSET_ORDER = {
    "clinical_knowledge": 0,
    "medical_lexicon_text": 1,
    "medical_lexicon_review": 2,
    "context_probe": 3,
}
EXPECTED_PRIVACY_EXCLUSIONS = [
    "contact_details",
    "credentials_or_secrets",
    "patient_identity",
    "sealed_holdout_content",
]
HUMAN_REVIEW_FIELDS = {"status", "reviewer_id", "reviewer_role", "reviewed_at"}


@dataclass(frozen=True)
class RuntimeLexiconPair:
    """Represent one executable rewrite from the checked-in text asset.
    Use it to compare the exact runtime pair with the human review ledger.
    Empty values are excluded by the parser and never reach user-facing checks.

    Attributes:
        canonical: Term the clinician would see; empty means invalid runtime data.
        variant: ASR phrase replaced live; empty means no executable pair exists.
    """

    canonical: str
    variant: str


def normalize_contract_text(value: str) -> str:
    """Normalize case and whitespace for stable pair or keyword identity.

    Args:
        value: Contract text; empty stays empty and fails its owning validator.

    Returns:
        Case-folded single-space text; never null.
    """
    return " ".join(value.split()).casefold()


def stable_json_bytes(value: Any) -> bytes:
    """Return canonical JSON bytes for repeatable reviewer evidence.

    Args:
        value: JSON-compatible report; null remains an explicit JSON null.

    Returns:
        Sorted compact UTF-8 JSON with one final newline; never empty for a report.
    """
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")


def add_finding(
    findings: list[dict[str, str]],
    finding_id: str,
    asset: str,
    entry_id: str,
    message: str,
) -> None:
    """Add one stable reviewer stop without duplicating the same defect.

    Args:
        findings: Current rows; empty means no known problem yet.
        finding_id: Frozen category; empty would not support remediation.
        asset: Owning input; empty would hide the user-facing source.
        entry_id: Affected card/pair; empty becomes an asset-level label.
        message: Plain-English UI impact; empty would not explain the stop.
    """
    visible_entry_id = entry_id or "<asset>"
    finding_identity = (finding_id, asset, visible_entry_id)
    # Two validator paths must not make one visible defect look like two failures.
    if any(
        (row["finding_id"], row["asset"], row["entry_id"]) == finding_identity
        for row in findings
    ):
        return
    findings.append(
        {
            "finding_id": finding_id,
            "asset": asset,
            "entry_id": visible_entry_id,
            "message": message,
        }
    )


def validate_exact_fields(
    document: dict[str, Any],
    expected_fields: set[str],
    asset: str,
    entry_id: str,
    findings: list[dict[str, str]],
) -> None:
    """Fail closed on missing or unknown fields in a frozen object.

    Args:
        document: Parsed object; empty means every required field is absent.
        expected_fields: Exact schema keys; empty would accept no useful data.
        asset: Owning input; empty cannot route repair work.
        entry_id: Row identity; empty means top-level validation.
        findings: Current report; empty means no earlier schema issue exists.
    """
    # Each missing field identifies why the card or pair cannot activate.
    for field_name in sorted(expected_fields - set(document)):
        add_finding(
            findings,
            "schema.missing_field",
            asset,
            entry_id,
            f"missing required field: {field_name}",
        )
    # Unknown fields cannot be ignored when they may be misspelled controls.
    for field_name in sorted(set(document) - expected_fields):
        add_finding(
            findings,
            "schema.unknown_field",
            asset,
            entry_id,
            f"unknown field: {field_name}",
        )


def has_text(value: Any) -> bool:
    """Return whether a reviewer-facing value contains usable text.

    Args:
        value: Candidate field; null, non-text, or whitespace means absent to a user.

    Returns:
        True only for non-whitespace strings.
    """
    return isinstance(value, str) and value.strip() != ""


def contains_complete_phrase(text: str, phrase: str) -> bool:
    """Match a keyword or variant only as a complete lexical phrase.

    Args:
        text: Source or guard wording; empty cannot match.
        phrase: Lookup phrase; empty never matches all text.

    Returns:
        True for a complete phrase, never a substring-only collision.
    """
    normalized_phrase = normalize_contract_text(phrase)
    # Empty input must not become the regular expression that matches everywhere.
    if normalized_phrase == "":
        return False
    escaped_phrase = re.escape(normalized_phrase).replace(r"\ ", r"\s+")
    return (
        re.search(rf"(?<!\w){escaped_phrase}(?!\w)", normalize_contract_text(text))
        is not None
    )


def validate_privacy(
    entry: dict[str, Any],
    asset: str,
    entry_id: str,
    findings: list[dict[str, str]],
) -> None:
    """Require exact privacy exclusions on one activatable row.

    Args:
        entry: Card or pair; empty has no privacy declaration.
        asset: Owning input; empty cannot identify the unsafe surface.
        entry_id: Reviewer row ID; empty blocks activation.
        findings: Current rows; empty means no earlier privacy issue exists.
    """
    # A different or empty list leaves user-facing input outside frozen protection.
    if entry.get("privacy_exclusions") != EXPECTED_PRIVACY_EXCLUSIONS:
        add_finding(
            findings,
            "privacy.invalid_exclusions",
            asset,
            entry_id,
            "privacy exclusions must match the frozen sorted list",
        )


def validate_human_review(
    review: Any,
    asset: str,
    entry_id: str,
    findings: list[dict[str, str]],
) -> str:
    """Validate one human review block and return its activation status.

    Args:
        review: Candidate object; null or empty means no approval exists.
        asset: Owning input; empty cannot route the finding.
        entry_id: Card/pair ID; empty means no accountable row.
        findings: Current rows; empty means no earlier review issue exists.

    Returns:
        Approved/pending/rejected, or empty for a malformed block.
    """
    # A non-object cannot prove who approved clinician-visible behavior.
    if not isinstance(review, dict):
        add_finding(
            findings,
            "review.missing_identity",
            asset,
            entry_id,
            "review must be an object",
        )
        return ""
    validate_exact_fields(review, HUMAN_REVIEW_FIELDS, asset, entry_id, findings)
    review_status = str(review.get("status", ""))
    # Unknown status cannot authorize a card or rewrite.
    if review_status not in {"approved", "pending", "rejected"}:
        add_finding(
            findings,
            "review.invalid_status",
            asset,
            entry_id,
            "review status must be approved, pending, or rejected",
        )
    # Empty reviewer identity leaves no accountable human for visible behavior.
    if not has_text(review.get("reviewer_id")) or not has_text(
        review.get("reviewer_role")
    ):
        add_finding(
            findings,
            "review.missing_identity",
            asset,
            entry_id,
            "reviewer ID and role must be non-empty",
        )
    # A malformed date cannot bind approval to the reviewed asset version.
    if DATE_PATTERN.fullmatch(str(review.get("reviewed_at", ""))) is None:
        add_finding(
            findings,
            "review.invalid_date",
            asset,
            entry_id,
            "reviewed_at must use YYYY-MM-DD",
        )
    return review_status
