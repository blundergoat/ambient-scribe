"""Validate optional clinical knowledge cards before prompt retrieval.

Use these rules to keep documentation reminders inactive, reviewed, and bounded.
The module checks card schema, source identity, keywords, guards, and sealed data.
It never treats a card as encounter evidence or renders it into a clinician note.
"""

from __future__ import annotations

from typing import Any

from clinical_data_audit_shared import (
    DATE_PATTERN,
    KNOWLEDGE_SCHEMA_VERSION,
    NORMALIZED_ID_PATTERN,
    SHA256_PATTERN,
    add_finding,
    contains_complete_phrase,
    has_text,
    normalize_contract_text,
    validate_exact_fields,
    validate_human_review,
    validate_privacy,
)

KNOWLEDGE_FIELDS = {
    "schema_version",
    "asset_id",
    "asset_version",
    "default_state",
    "entries",
}
KNOWLEDGE_ENTRY_FIELDS = {
    "id",
    "status",
    "title",
    "keywords",
    "snippet",
    "hard_negatives",
    "evidence_class",
    "locale",
    "intended_consumer",
    "source",
    "review",
    "privacy_exclusions",
}
KNOWLEDGE_SOURCE_FIELDS = {
    "artifact_id",
    "locator",
    "sha256",
    "published_at",
    "updated_at",
}


def _validate_source(
    source: Any,
    entry_id: str,
    sealed_stems: frozenset[str],
    findings: list[dict[str, str]],
) -> None:
    """Validate the reviewed document behind one optional prompt card.

    Args:
        source: Source object; null or empty means the card has no authority.
        entry_id: Card ID; empty cannot route the reviewer finding.
        sealed_stems: Forbidden source IDs; empty skips only identity comparison.
        findings: Current rows; empty means no earlier source issue exists.
    """
    # A non-object source cannot prove where the reminder came from.
    if not isinstance(source, dict):
        add_finding(
            findings,
            "knowledge.invalid_source",
            "clinical_knowledge",
            entry_id,
            "source must be an object",
        )
        return
    validate_exact_fields(
        source,
        KNOWLEDGE_SOURCE_FIELDS,
        "clinical_knowledge",
        entry_id,
        findings,
    )
    source_id = str(source.get("artifact_id", ""))
    source_locator = str(source.get("locator", ""))
    # A sealed identity or locator can never authorize development context.
    if source_id in sealed_stems or any(
        sealed_stem in source_locator for sealed_stem in sealed_stems
    ):
        add_finding(
            findings,
            "privacy.sealed_holdout_source",
            "clinical_knowledge",
            entry_id,
            "knowledge source points at a sealed holdout",
        )
    # A malformed digest cannot bind the card to reviewed source bytes.
    if SHA256_PATTERN.fullmatch(str(source.get("sha256", ""))) is None:
        add_finding(
            findings,
            "knowledge.invalid_source",
            "clinical_knowledge",
            entry_id,
            "source SHA-256 must be 64 lowercase hexadecimal characters",
        )
    # Both dates identify the exact guidance version a reviewer approved.
    for date_field in ("published_at", "updated_at"):
        # Missing or malformed date leaves source version ambiguous.
        if DATE_PATTERN.fullmatch(str(source.get(date_field, ""))) is None:
            add_finding(
                findings,
                "knowledge.invalid_source",
                "clinical_knowledge",
                entry_id,
                f"{date_field} must use YYYY-MM-DD",
            )


def _audit_keywords(
    entry: dict[str, Any],
    entry_id: str,
    findings: list[dict[str, str]],
) -> list[str]:
    """Validate one card's retrieval phrases and unrelated guards.

    Args:
        entry: Knowledge card; empty has no retrieval terms.
        entry_id: Card ID; empty cannot route a finding.
        findings: Current rows; empty means no earlier keyword issue exists.

    Returns:
        Normalized keywords; empty means no usable retrieval phrase.
    """
    keywords = entry.get("keywords")
    # Empty or malformed keywords make retrieval unpredictable.
    if not isinstance(keywords, list) or not keywords:
        add_finding(
            findings,
            "knowledge.invalid_keywords",
            "clinical_knowledge",
            entry_id,
            "keywords must contain one to twelve phrases",
        )
        keywords = []
    normalized_keywords = [
        normalize_contract_text(str(keyword))
        # Each keyword gets one stable cross-card identity.
        for keyword in keywords
    ]
    # Too many, empty, or repeated terms cannot form a safe retrieval set.
    if (
        len(normalized_keywords) > 12
        or "" in normalized_keywords
        or len(normalized_keywords) != len(set(normalized_keywords))
    ):
        add_finding(
            findings,
            "knowledge.invalid_keywords",
            "clinical_knowledge",
            entry_id,
            "keywords must be non-empty, unique, and limited to twelve",
        )
    hard_negatives = entry.get("hard_negatives")
    # No unrelated sentence means over-retrieval has not been checked.
    if not isinstance(hard_negatives, list) or not hard_negatives:
        add_finding(
            findings,
            "knowledge.missing_hard_negative",
            "clinical_knowledge",
            entry_id,
            "each card needs an unrelated hard-negative sentence",
        )
        hard_negatives = []
    # Every guard must remain outside complete-phrase retrieval.
    for hard_negative in hard_negatives:
        # A complete keyword occurrence would retrieve an unrelated card.
        if any(
            contains_complete_phrase(str(hard_negative), keyword)
            for keyword in normalized_keywords
        ):
            add_finding(
                findings,
                "knowledge.hard_negative_matches",
                "clinical_knowledge",
                entry_id,
                "a hard negative contains a complete card keyword",
            )
    return normalized_keywords


def _audit_entry(
    entry: dict[str, Any],
    sealed_stems: frozenset[str],
    findings: list[dict[str, str]],
) -> tuple[str, list[str], str, str]:
    """Validate one card and return ID, keywords, entry state, and review state.

    Args:
        entry: Parsed card; empty fails its required fields.
        sealed_stems: Forbidden source IDs; empty skips only identity comparison.
        findings: Current rows; empty means no earlier card issue exists.

    Returns:
        Card ID, normalized keywords, status, and review status; empty states fail closed.
    """
    entry_id = str(entry.get("id", ""))
    validate_exact_fields(
        entry, KNOWLEDGE_ENTRY_FIELDS, "clinical_knowledge", entry_id, findings
    )
    entry_status = str(entry.get("status", ""))
    # Unknown status cannot decide whether a card may enter the prompt.
    if entry_status not in {"active", "inactive", "rejected"}:
        add_finding(
            findings,
            "knowledge.invalid_status",
            "clinical_knowledge",
            entry_id,
            "status must be active, inactive, or rejected",
        )
    review_status = validate_human_review(
        entry.get("review"), "clinical_knowledge", entry_id, findings
    )
    # Active cards without approval must remain invisible to note generation.
    if entry_status == "active" and review_status != "approved":
        add_finding(
            findings,
            "knowledge.unreviewed_active",
            "clinical_knowledge",
            entry_id,
            "active knowledge requires approved review",
        )
    # Wrong locale or consumer could affect a different user workflow.
    if (
        entry.get("locale") != "en-AU"
        or entry.get("intended_consumer") != "summary_prompt_context"
    ):
        add_finding(
            findings,
            "knowledge.invalid_consumer",
            "clinical_knowledge",
            entry_id,
            "knowledge requires en-AU and summary_prompt_context",
        )
    # Missing title/snippet leaves no usable reminder for a reviewer.
    if not has_text(entry.get("title")) or not has_text(entry.get("snippet")):
        add_finding(
            findings,
            "knowledge.missing_visible_text",
            "clinical_knowledge",
            entry_id,
            "title and snippet must be non-empty",
        )
    # Unsupported evidence cannot authorize a documentation reminder.
    if entry.get("evidence_class") not in {
        "authoritative_guidance",
        "peer_reviewed_reference",
        "project_authored_documentation_checklist",
    }:
        add_finding(
            findings,
            "knowledge.invalid_evidence_class",
            "clinical_knowledge",
            entry_id,
            "knowledge evidence class is not approved",
        )
    normalized_keywords = _audit_keywords(entry, entry_id, findings)
    _validate_source(entry.get("source"), entry_id, sealed_stems, findings)
    validate_privacy(entry, "clinical_knowledge", entry_id, findings)
    return (
        entry_id,
        normalized_keywords,
        entry_status,
        review_status,
    )


def _audit_collisions(
    keyword_owners: list[tuple[str, str]], findings: list[dict[str, str]]
) -> None:
    """Report each strict cross-card substring collision once.

    Args:
        keyword_owners: Normalized keyword/card pairs; empty means no terms.
        findings: Current rows; empty means no earlier collision exists.
    """
    seen_collisions: set[tuple[str, str]] = set()
    # Each keyword is compared only with later rows to avoid duplicates.
    for left_index, (left_keyword, _left_owner) in enumerate(keyword_owners):
        # Later rows exclude self-comparison but retain cross-card checks.
        for right_keyword, _right_owner in keyword_owners[left_index + 1 :]:
            # Equal terms are duplicates, not strict substrings.
            if left_keyword == right_keyword:
                continue
            shorter, longer = sorted(
                (left_keyword, right_keyword), key=lambda item: (len(item), item)
            )
            # Non-embedded or already-reported phrases add no new reviewer issue.
            if shorter not in longer or (shorter, longer) in seen_collisions:
                continue
            seen_collisions.add((shorter, longer))
            add_finding(
                findings,
                "knowledge.keyword_substring_collision",
                "clinical_knowledge",
                f"{shorter}::{longer}",
                "one normalized keyword is a strict substring of another",
            )


def audit_clinical_knowledge(
    document: dict[str, Any],
    sealed_stems: frozenset[str],
    findings: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], int]:
    """Validate prompt cards without treating them as patient truth.

    Args:
        document: Parsed knowledge asset; empty fails closed.
        sealed_stems: Forbidden source IDs; empty skips only identity comparison.
        findings: Current rows; empty means no earlier asset issue exists.

    Returns:
        Structured entries and count eligible only when context is enabled later.
    """
    validate_exact_fields(
        document, KNOWLEDGE_FIELDS, "clinical_knowledge", "", findings
    )
    # Unknown schema rules cannot authorize any prompt card.
    if document.get("schema_version") != KNOWLEDGE_SCHEMA_VERSION:
        add_finding(
            findings,
            "schema.invalid_version",
            "clinical_knowledge",
            "",
            f"expected {KNOWLEDGE_SCHEMA_VERSION}",
        )
    # Knowledge stays off until a request explicitly enables its internal seam.
    if document.get("default_state") != "inactive":
        add_finding(
            findings,
            "knowledge.default_state_not_inactive",
            "clinical_knowledge",
            "",
            "knowledge must remain inactive by default",
        )
    raw_entries = document.get("entries")
    # A malformed list authorizes no clinician-visible reminder.
    if not isinstance(raw_entries, list):
        add_finding(
            findings,
            "schema.invalid_entries",
            "clinical_knowledge",
            "",
            "entries must be a list",
        )
        return [], 0
    entries: list[dict[str, Any]] = []
    entry_ids: list[str] = []
    keyword_owners: list[tuple[str, str]] = []
    eligible_count = 0
    # Every card is checked independently so a bad row cannot hide another.
    for raw_entry in raw_entries:
        # Non-object rows cannot describe what a clinician would see.
        if not isinstance(raw_entry, dict):
            add_finding(
                findings,
                "schema.invalid_entry",
                "clinical_knowledge",
                "",
                "each knowledge entry must be an object",
            )
            continue
        entries.append(raw_entry)
        entry_id, keywords, entry_status, review_status = _audit_entry(
            raw_entry, sealed_stems, findings
        )
        # Malformed or repeated IDs make reviewer ownership ambiguous.
        if NORMALIZED_ID_PATTERN.fullmatch(entry_id) is None or entry_id in entry_ids:
            add_finding(
                findings,
                "knowledge.invalid_or_duplicate_id",
                "clinical_knowledge",
                entry_id,
                "knowledge IDs must be unique lower-case hyphenated words",
            )
        entry_ids.append(entry_id)
        eligible_count += int(entry_status == "active" and review_status == "approved")
        # Each normalized term keeps its owner for cross-card checks.
        for keyword in keywords:
            keyword_owners.append((keyword, entry_id))
    # Source order is frozen so repeated reviewer output stays byte-identical.
    if entry_ids != sorted(entry_ids, key=normalize_contract_text):
        add_finding(
            findings,
            "knowledge.invalid_order",
            "clinical_knowledge",
            "",
            "entries must sort by normalized ID",
        )
    _audit_collisions(keyword_owners, findings)
    return entries, eligible_count
