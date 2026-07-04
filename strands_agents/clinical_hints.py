"""
Clinical hint and tiny RAG helpers for the assistive UI lane.

This module is deliberately CPU-only and rule based for the PoC. It retrieves
short synthetic knowledge snippets and emits clinician-review suggestions that
can be published on the Mercure hints topic without touching the NeMo GPU.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def default_clinical_knowledge_path() -> Path:
    """Return the bundled PoC knowledge-base path.

    Returns:
        Path to curated synthetic KB snippets used by summary grounding and hints.
    """
    return Path(__file__).resolve().parent / "data" / "clinical_knowledge.json"


def load_clinical_knowledge(path: str | Path | None = None) -> list[dict[str, Any]]:
    """Load the tiny clinical knowledge base used for suggestions.

    Args:
        path: Optional KB path; null uses the bundled PoC corpus.

    Returns:
        Knowledge snippets; empty means summaries and hints run without RAG context.
    """
    knowledge_path = Path(path) if path else default_clinical_knowledge_path()
    # Missing KB should not stop summaries or transcript rendering.
    if not knowledge_path.exists():
        return []

    try:
        payload = json.loads(knowledge_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
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


def generate_clinical_hints(transcript: str) -> list[dict[str, str]]:
    """Create clinician-review suggestions from transcript text and KB rules.

    Args:
        transcript: Role-attributed text; blank produces no hints.

    Returns:
        Structured hints; empty means the sidebar remains hidden.
    """
    # Without transcript text, there is no evidence span to show the clinician.
    if not transcript.strip():
        return []

    transcript_lower = transcript.lower()
    hints: list[dict[str, str]] = []

    # NSAID plus ACE inhibitor can matter for renal monitoring in common practice.
    if "naproxen" in transcript_lower and "lisinopril" in transcript_lower:
        hints.append(
            {
                "type": "drug_interaction",
                "text": "Review renal risk when naproxen is used with lisinopril.",
                "evidence_span": "naproxen + lisinopril",
            }
        )

    # Chest-pain sessions should usually document objective cardiac checks.
    if "chest pain" in transcript_lower and "ecg" not in transcript_lower:
        hints.append(
            {
                "type": "missing_objective",
                "text": "Consider documenting objective cardiac assessment such as ECG or vitals.",
                "evidence_span": "chest pain",
            }
        )

    # Medication changes need explicit follow-up in the clinician note.
    if any(
        term in transcript_lower
        for term in ("prescribing", "prescribe", "start ", "add ")
    ):
        # A transcript with no follow-up language should prompt a review reminder.
        if "follow up" not in transcript_lower and "review" not in transcript_lower:
            hints.append(
                {
                    "type": "follow_up",
                    "text": "Consider adding a follow-up plan for the medication change.",
                    "evidence_span": "medication change",
                }
            )

    return hints
