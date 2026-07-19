"""
Clinical-context safety and retrieval for generated visit summaries.

The normal clinician workflow keeps optional knowledge cards off. Approved
internal probes may retrieve reviewed, schema-valid documentation reminders
without touching the transcription GPU or treating reminders as patient evidence.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


_KNOWLEDGE_SCHEMA_VERSION = "ambient-scribe-clinical-knowledge/v1"
_KNOWLEDGE_FIELDS = {
    "schema_version",
    "asset_id",
    "asset_version",
    "default_state",
    "entries",
}
_KNOWLEDGE_ENTRY_FIELDS = {
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
_KNOWLEDGE_SOURCE_FIELDS = {
    "artifact_id",
    "locator",
    "sha256",
    "published_at",
    "updated_at",
}
_KNOWLEDGE_REVIEW_FIELDS = {
    "status",
    "reviewer_id",
    "reviewer_role",
    "reviewed_at",
}
_PRIVACY_EXCLUSIONS = [
    "contact_details",
    "credentials_or_secrets",
    "patient_identity",
    "sealed_holdout_content",
]
_KNOWLEDGE_ID_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")


def default_clinical_knowledge_path() -> Path:
    """Return the bundled PoC knowledge-base path.

    Returns:
        Path to curated synthetic KB snippets used by summary grounding.
    """
    return Path(__file__).resolve().parent / "data" / "clinical_knowledge.json"


def _has_text(value: Any) -> bool:
    """Return whether a reviewed card field can be shown or audited.

    Empty, null, or non-text values mean the card stays out of the user's note.
    """
    return isinstance(value, str) and value.strip() != ""


def _review_is_schema_valid(review: Any) -> bool:
    """Accept an accountable review block before a card can reach a note.

    Null or malformed review data keeps the reminder invisible to the user.
    """
    # A missing or expanded review block cannot prove who approved visible wording.
    if not isinstance(review, dict) or set(review) != _KNOWLEDGE_REVIEW_FIELDS:
        return False
    # Only a named reviewer and a dated frozen status can govern an internal context probe.
    return (
        review.get("status") in {"approved", "pending", "rejected"}
        and _has_text(review.get("reviewer_id"))
        and _has_text(review.get("reviewer_role"))
        and _DATE_PATTERN.fullmatch(str(review.get("reviewed_at", ""))) is not None
    )


def _source_is_schema_valid(source: Any) -> bool:
    """Accept a source identity that binds a reviewed documentation card.

    Null or incomplete provenance keeps the card out of the clinician's prompt.
    """
    # A missing or expanded source block cannot identify the reviewed material safely.
    if not isinstance(source, dict) or set(source) != _KNOWLEDGE_SOURCE_FIELDS:
        return False
    # Complete identity, digest, and dates are required before an internal probe can use the card.
    return (
        _has_text(source.get("artifact_id"))
        and _has_text(source.get("locator"))
        and _SHA256_PATTERN.fullmatch(str(source.get("sha256", ""))) is not None
        and _DATE_PATTERN.fullmatch(str(source.get("published_at", ""))) is not None
        and _DATE_PATTERN.fullmatch(str(source.get("updated_at", ""))) is not None
    )


def _normalize_contract_text(value: str) -> str:
    """Normalize reviewed phrases before deterministic safety comparisons.

    Empty input stays empty and makes its owning card ineligible for the user.
    """
    return " ".join(value.split()).casefold()


def _normalized_keywords(entry: dict[str, Any]) -> list[str] | None:
    """Return ordered unique retrieval phrases for one reviewed card.

    Null means the card's keywords are unsafe, so the user receives no reminder.
    """
    keywords = entry.get("keywords")
    # Missing or excessive keywords make retrieval unpredictable for the user.
    if not isinstance(keywords, list) or not 1 <= len(keywords) <= 12:
        return None
    normalized_keywords = [
        _normalize_contract_text(keyword)
        # Each non-empty phrase gets one identity for ordering and collision checks.
        for keyword in keywords
        if isinstance(keyword, str) and keyword.strip() != ""
    ]
    # Filtered, duplicate, or unsorted phrases cannot pass as reviewed input.
    if (
        len(normalized_keywords) != len(keywords)
        or len(normalized_keywords) != len(set(normalized_keywords))
        or normalized_keywords != sorted(normalized_keywords)
    ):
        return None
    return normalized_keywords


def _hard_negatives_are_schema_valid(
    entry: dict[str, Any], normalized_keywords: list[str]
) -> bool:
    """Prove each retrieval phrase stays out of unrelated user examples.

    Empty or matching guards make the card ineligible for an internal prompt.
    """
    hard_negatives = entry.get("hard_negatives")
    # Every keyword needs an ordered unrelated sentence proving safe retrieval.
    if not isinstance(hard_negatives, list) or len(hard_negatives) < len(
        normalized_keywords
    ):
        return False
    normalized_hard_negatives = [
        _normalize_contract_text(sentence)
        # Blank or non-text guards cannot demonstrate safe behavior to a reviewer.
        for sentence in hard_negatives
        if isinstance(sentence, str) and sentence.strip() != ""
    ]
    # Filtered, duplicate, or unsorted guards make the reviewed asset ambiguous.
    if (
        len(normalized_hard_negatives) != len(hard_negatives)
        or len(normalized_hard_negatives) != len(set(normalized_hard_negatives))
        or normalized_hard_negatives != sorted(normalized_hard_negatives)
    ):
        return False
    # Each unrelated example must remain outside complete-phrase retrieval.
    for hard_negative in normalized_hard_negatives:
        # Every reviewed keyword is checked independently against this user example.
        for keyword in normalized_keywords:
            escaped_keyword = re.escape(keyword).replace(r"\ ", r"\s+")
            # A complete match proves that the card would appear for unrelated text.
            if re.search(rf"(?<!\w){escaped_keyword}(?!\w)", hard_negative):
                return False
    return True


def _visible_entry_fields_are_schema_valid(entry: dict[str, Any]) -> bool:
    """Validate wording and routing fields before a reminder can reach a note.

    Empty or unsupported values leave the user's generated summary unchanged.
    """
    snippet = entry.get("snippet")
    # Visible wording must exist and remain within the reviewed prompt budget.
    if (
        not _has_text(entry.get("title"))
        or not _has_text(snippet)
        or len(str(snippet)) > 320
    ):
        return False
    # Only the frozen locale, consumer, and evidence classes belong to this note flow.
    return (
        entry.get("locale") == "en-AU"
        and entry.get("intended_consumer") == "summary_prompt_context"
        and entry.get("evidence_class")
        in {
            "authoritative_guidance",
            "peer_reviewed_reference",
            "project_authored_documentation_checklist",
        }
    )


def _entry_governance_is_schema_valid(entry: dict[str, Any]) -> bool:
    """Validate provenance, review state, and privacy before internal enablement.

    Missing governance keeps the reminder invisible in the clinician's note.
    """
    # Every card needs complete provenance, accountable review, and exact privacy exclusions.
    if (
        not _source_is_schema_valid(entry.get("source"))
        or not _review_is_schema_valid(entry.get("review"))
        or entry.get("privacy_exclusions") != _PRIVACY_EXCLUSIONS
    ):
        return False
    # An active reminder cannot influence a note without explicit human approval.
    if entry.get("status") == "active" and entry["review"].get("status") != "approved":
        return False
    return True


def _entry_is_schema_valid(entry: Any) -> bool:
    """Validate one optional reminder before it can influence a generated note.

    Any absent, unknown, or unsafe field makes the whole asset fail closed for the user.
    """
    # A non-object or unknown field cannot describe an approved user-visible reminder.
    if not isinstance(entry, dict) or set(entry) != _KNOWLEDGE_ENTRY_FIELDS:
        return False
    # Stable identity and supported state keep reviewer decisions attached to one card.
    if _KNOWLEDGE_ID_PATTERN.fullmatch(str(entry.get("id", ""))) is None or entry.get(
        "status"
    ) not in {"active", "inactive", "rejected"}:
        return False
    normalized_keywords = _normalized_keywords(entry)
    # Invalid retrieval phrases prevent hard-negative proof and make the card ineligible.
    if normalized_keywords is None:
        return False
    return (
        _hard_negatives_are_schema_valid(entry, normalized_keywords)
        and _visible_entry_fields_are_schema_valid(entry)
        and _entry_governance_is_schema_valid(entry)
    )


def _document_header_is_schema_valid(document: Any) -> bool:
    """Validate the inactive asset header before any card is considered.

    Empty or unknown controls keep all documentation reminders out of the user's note.
    """
    # Unknown top-level shape or schema controls make every card ineligible.
    if not isinstance(document, dict) or set(document) != _KNOWLEDGE_FIELDS:
        return False
    return (
        document.get("schema_version") == _KNOWLEDGE_SCHEMA_VERSION
        and document.get("default_state") == "inactive"
        and _KNOWLEDGE_ID_PATTERN.fullmatch(str(document.get("asset_id", "")))
        is not None
        and _has_text(document.get("asset_version"))
    )


def _has_deterministic_entry_ids(entries: list[dict[str, Any]]) -> bool:
    """Keep card identity unique and ordered for repeatable user-visible context.

    Empty entries are valid and mean no reminder can be shown.
    """
    entry_ids = [str(entry["id"]) for entry in entries]
    return len(entry_ids) == len(set(entry_ids)) and entry_ids == sorted(entry_ids)


def _has_safe_keyword_ownership(entries: list[dict[str, Any]]) -> bool:
    """Reject phrases that could select two different reminders for one user input.

    Empty entry lists are collision-free and leave the normal note path unchanged.
    """
    keyword_owners = [
        (_normalize_contract_text(keyword), str(entry["id"]))
        # Every phrase retains its card owner for cross-card comparison.
        for entry in entries
        for keyword in entry["keywords"]
    ]
    # Compare each phrase with later cards so a collision is evaluated once.
    for left_index, (left_keyword, left_owner) in enumerate(keyword_owners):
        # Later phrases avoid self-comparison and duplicate pair reporting.
        for right_keyword, right_owner in keyword_owners[left_index + 1 :]:
            # Phrases on the same card were already checked for uniqueness.
            if left_owner == right_owner:
                continue
            shorter_keyword, longer_keyword = sorted(
                (left_keyword, right_keyword), key=lambda value: (len(value), value)
            )
            # Equal or embedded phrases could select the wrong reminder for the user.
            if left_keyword == right_keyword or shorter_keyword in longer_keyword:
                return False
    return True


def _schema_eligible_entries(document: Any) -> list[dict[str, Any]]:
    """Return only reviewed active cards from one exact inactive-by-default asset.

    Empty or malformed assets leave the user's normal summary prompt unchanged.
    """
    # An invalid asset header authorizes no documentation reminder.
    if not _document_header_is_schema_valid(document):
        return []
    entries = document.get("entries")
    # A missing list or one malformed card invalidates the reviewed asset as a unit.
    if not isinstance(entries, list) or any(
        not _entry_is_schema_valid(entry) for entry in entries
    ):
        return []
    # Reordered IDs or overlapping keywords could show the wrong reminder to the user.
    if not _has_deterministic_entry_ids(entries) or not _has_safe_keyword_ownership(
        entries
    ):
        return []
    # Only active and approved cards can appear when an internal caller enables context.
    return [
        entry
        for entry in entries
        if entry["status"] == "active" and entry["review"]["status"] == "approved"
    ]


def load_clinical_knowledge(path: str | Path | None = None) -> list[dict[str, Any]]:
    """Load the tiny clinical knowledge base used for summary context.

    Args:
        path: Optional KB path; null uses the bundled PoC corpus.

    Returns:
        Knowledge snippets; empty means summaries run without RAG context.
    """
    knowledge_path = Path(path) if path else default_clinical_knowledge_path()
    # Missing KB should not stop summaries or transcript rendering.
    if not knowledge_path.exists():
        return []

    try:
        payload = json.loads(knowledge_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # For example, a partial asset deployment leaves the user's note transcript-only.
        return []
    # Schema-invalid, inactive, rejected, or unreviewed cards never reach the note prompt.
    return _schema_eligible_entries(payload)


def retrieve_clinical_context(
    transcript: str,
    limit: int = 3,
    knowledge_entries: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """Return KB snippets relevant to the current consultation transcript.

    Args:
        transcript: Role-attributed text; blank produces no context.
        limit: Maximum snippets added to the summary prompt; zero returns none.
        knowledge_entries: Optional schema-valid test/probe cards; null loads the bundled asset.

    Returns:
        Relevant snippets; empty means the summary prompt is ungrounded.
    """
    # Blank transcripts have no clinical terms to retrieve against.
    if not transcript.strip() or limit <= 0:
        return []

    # The normal note path loads the validated bundled asset; internal probes must supply equally valid cards.
    if knowledge_entries is None:
        entries = load_clinical_knowledge()
    else:
        entries = _schema_eligible_entries(
            {
                "schema_version": _KNOWLEDGE_SCHEMA_VERSION,
                "asset_id": "internal-context-probe",
                "asset_version": "runtime-probe",
                "default_state": "inactive",
                "entries": knowledge_entries,
            }
        )
    transcript_lower = transcript.lower()
    scored_entries: list[tuple[int, dict[str, Any]]] = []

    # Score each KB entry by keyword hits in the visible transcript.
    for entry in entries:
        keywords = entry.get("keywords", [])
        # Entries without keyword lists cannot be matched safely.
        if not isinstance(keywords, list):
            continue

        score = sum(
            1 for keyword in keywords if str(keyword).lower() in transcript_lower
        )
        # Only matched entries should influence the clinician-facing summary.
        if score > 0:
            scored_entries.append((score, entry))

    scored_entries.sort(key=lambda item: item[0], reverse=True)
    # The prompt receives only the fields needed to show one bounded documentation reminder.
    return [
        {
            "id": str(entry.get("id", "")),
            "title": str(entry.get("title", "")),
            "snippet": str(entry.get("snippet", "")),
            "provenance": str(entry["source"]["locator"]),
        }
        for _, entry in scored_entries[:limit]
    ]
