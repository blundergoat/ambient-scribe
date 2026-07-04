"""
GPU-free role heuristics for visible medical speaker labels.

The live role agent uses Strands when available, but this module gives the UI a fallback
that can run in tests, eval scripts, and local demos without loading FastAPI, NeMo, or an LLM.
Use it when the browser needs DOCTOR/PATIENT labels and model inference is unavailable.
"""

from __future__ import annotations

from typing import Any

DOCTOR_KEYWORDS = {
    "prescribe",
    "diagnosis",
    "symptoms",
    "mg",
    "dosage",
    "treatment plan",
    "medication",
}
PATIENT_KEYWORDS = {
    "i feel",
    "my pain",
    "hurts",
    "i've been feeling",
    "it hurts",
    "i have a",
}


def heuristic_role_inference(
    segments: list[dict[str, Any]],
    transcript: str,
) -> dict[str, Any] | None:
    """Infer DOCTOR/PATIENT labels from visible transcript text.

    Args:
        segments: Transcript segments shown in the UI; empty falls back to transcript text only.
        transcript: Plain transcript text; blank plus no segments means no role evidence exists.

    Returns:
        Role mapping payload, or `None` when the UI should keep raw speaker labels.
    """
    # No transcript evidence means the browser should keep raw speaker labels.
    if not segments and not transcript.strip():
        return None

    speaker_texts, speaker_order = _collect_visible_speaker_text(segments)
    mapping = _keyword_role_mapping(speaker_texts)
    _fill_missing_visible_roles(mapping, speaker_order)

    # No mapping means the UI should not pretend it knows any clinical roles.
    if not mapping:
        return None

    return {
        "mapping": mapping,
        "confidence": 0.4,
        "reasoning": "Heuristic keyword-based assignment for a medical consultation",
    }


def _collect_visible_speaker_text(
    segments: list[dict[str, Any]],
) -> tuple[dict[str, str], list[str]]:
    """Group visible transcript text by speaker for fallback role labels.

    Args:
        segments: Transcript rows shown in the browser; empty leaves no speaker evidence.

    Returns:
        Speaker text and first-seen order; both empty means no stable mapping can be inferred.
    """
    speaker_texts: dict[str, str] = {}
    speaker_order: list[str] = []
    # Each visible segment contributes words to that speaker's role evidence.
    for segment in segments:
        speaker_id = str(segment.get("speaker_id", ""))
        visible_text = str(segment.get("text", ""))
        # Blank speaker labels cannot become a stable DOCTOR/PATIENT mapping.
        if speaker_id:
            speaker_texts.setdefault(speaker_id, "")
            speaker_texts[speaker_id] += " " + visible_text
            # First-seen order lets the UI still receive useful labels when keywords are weak.
            if speaker_id not in speaker_order:
                speaker_order.append(speaker_id)

    return speaker_texts, speaker_order


def _keyword_role_mapping(speaker_texts: dict[str, str]) -> dict[str, str]:
    """Assign labels when a speaker uses doctor-like or patient-like wording.

    Args:
        speaker_texts: Text grouped by speaker; empty returns no keyword mapping.

    Returns:
        Partial mapping; empty means first-seen fallback should decide visible labels.
    """
    mapping: dict[str, str] = {}
    # Compare each speaker's words against small medical role vocabularies.
    for speaker_id, visible_text in speaker_texts.items():
        text_lower = visible_text.lower()
        doctor_score = sum(1 for keyword in DOCTOR_KEYWORDS if keyword in text_lower)
        patient_score = sum(1 for keyword in PATIENT_KEYWORDS if keyword in text_lower)
        # Doctor-heavy text gets a clinician label immediately.
        if doctor_score > patient_score:
            mapping[speaker_id] = "DOCTOR"
        # Patient-heavy text gets the patient label shown in the transcript.
        elif patient_score > doctor_score:
            mapping[speaker_id] = "PATIENT"

    return mapping


def _fill_missing_visible_roles(
    mapping: dict[str, str],
    speaker_order: list[str],
) -> None:
    """Fill missing labels so a two-speaker consultation still looks medical.

    Args:
        mapping: Existing keyword labels; empty means first-seen speaker becomes doctor.
        speaker_order: Visible speaker order; empty leaves the mapping unchanged.
    """
    assigned_roles = set(mapping.values())
    # Speakers without keyword evidence still need predictable medical labels in the UI.
    for speaker_id in speaker_order:
        # Already classified speakers keep the stronger keyword-driven label.
        if speaker_id not in mapping:
            # The first unassigned speaker becomes the doctor when no doctor clue was found.
            if "DOCTOR" not in assigned_roles:
                mapping[speaker_id] = "DOCTOR"
                assigned_roles.add("DOCTOR")
            # The next unassigned speaker becomes the patient when no patient clue was found.
            elif "PATIENT" not in assigned_roles:
                mapping[speaker_id] = "PATIENT"
                assigned_roles.add("PATIENT")
            else:
                mapping[speaker_id] = "PATIENT"
