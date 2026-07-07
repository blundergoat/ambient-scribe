"""
Tiny clinical-context retrieval helpers for summary generation.

This module is deliberately CPU-only and rule based for the PoC. It retrieves
short synthetic knowledge snippets that can ground the post-visit summary
without touching the NeMo GPU.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def default_clinical_knowledge_path() -> Path:
    """Return the bundled PoC knowledge-base path.

    Returns:
        Path to curated synthetic KB snippets used by summary grounding.
    """
    return Path(__file__).resolve().parent / "data" / "clinical_knowledge.json"


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
        return []
    # Valid JSON that is not an object (array, string, number) has no entries.
    if not isinstance(payload, dict):
        return []
    entries = payload.get("entries", [])
    # Invalid KB shape degrades to no retrieval rather than unsafe suggestions.
    if not isinstance(entries, list):
        return []

    return [entry for entry in entries if isinstance(entry, dict)]


def retrieve_clinical_context(
    transcript: str,
    limit: int = 3,
    knowledge_entries: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """Return KB snippets relevant to the current consultation transcript.

    Args:
        transcript: Role-attributed text; blank produces no context.
        limit: Maximum snippets added to the summary prompt; zero returns none.
        knowledge_entries: Optional test/probe corpus; null loads the bundled KB.

    Returns:
        Relevant snippets; empty means the summary prompt is ungrounded.
    """
    # Blank transcripts have no clinical terms to retrieve against.
    if not transcript.strip() or limit <= 0:
        return []

    entries = (
        knowledge_entries
        if knowledge_entries is not None
        else load_clinical_knowledge()
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
    return [
        {
            "id": str(entry.get("id", "")),
            "title": str(entry.get("title", "")),
            "snippet": str(entry.get("snippet", "")),
            "provenance": str(entry.get("provenance", "")),
        }
        for _, entry in scored_entries[:limit]
    ]
