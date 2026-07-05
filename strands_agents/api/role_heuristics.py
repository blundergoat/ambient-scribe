"""
GPU-free role heuristics for visible medical speaker labels.

The live role agent uses Strands when available, but this module gives the UI a fallback
that can run in tests, eval scripts, and local demos without loading FastAPI, NeMo, or an LLM.
Use it when the browser needs DOCTOR/PATIENT labels and model inference is unavailable.
It also owns the M20 row-exception lane: cheap, explainable text cues that catch single
transcript rows whose words contradict their speaker's mapped role (a doctor question
rendered on a Patient card) and either relabel or explicitly un-label just that row.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

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


# --- M20 row-exception cue lane ---------------------------------------------
# Thresholds and single-cue flip rules were measured on the Phase 0 baseline
# corpus (all PriMock fixtures): the shipped policy produced zero wrong flips
# corpus-wide. See the M20 plan's Phase 3 evidence before tuning.

# Rows shorter than this are often seam-smeared fragments whose words do not
# reliably belong to the labeled time span; the row lane leaves them alone.
ROW_CUE_MIN_WORDS = 5
# Two independent contradicting cues always justify relabeling a row.
ROW_CUE_MIN_FLIP_CUES = 2
# Payload sanity bound; a session needing more exceptions than this is logged.
ROW_EXCEPTIONS_MAX = 50

ROW_DOCTOR_CUES: dict[str, re.Pattern[str]] = {
    # "are you able to describe...", "do you smoke" - clinician interviewing.
    "you_question": re.compile(
        r"\b(are|do|did|have|has|can|could|would|will) you\b", re.I
    ),
    # "your neck", "your vision" - clinician referencing the patient's body.
    "second_person_body": re.compile(
        r"\byour (neck|head|eyes?|vision|chest|stomach|tummy|back|skin|arms?"
        r"|legs?|throat|ears?|nose|breathing|pain|symptoms?)\b",
        re.I,
    ),
    # "Okay. Is that when...?" - interrogative opening ending in a question.
    "clinical_question_mark": re.compile(
        r"^((okay|right|so|and|um|uh)[,.]? +)*"
        r"(is|was|are|does|do|any|how|what|when|where|which)\b.*\?",
        re.I,
    ),
    # "I'll prescribe...", "what brings you" - clinician plans and openers.
    "plan_language": re.compile(
        r"\b(i'?ll (prescribe|refer|examine|write)|let'?s (have|take) a look"
        r"|what brings you)\b",
        re.I,
    ),
    # "it's Dr. Seed here" - clinician identifies themselves to the patient.
    "clinician_self_intro": re.compile(
        r"\b(it'?s|i am|i'?m) +(dr\.?|doctor)\b"
        r"|\b(dr\.?|doctor) +[a-z][a-z'.-]* +here\b",
        re.I,
    ),
    # "How can I help you?" - standard consultation opener from the clinician.
    "consultation_opener": re.compile(
        r"\b(how can i help you|what can i do for you)\b",
        re.I,
    ),
}
ROW_PATIENT_CUES: dict[str, re.Pattern[str]] = {
    # "I've been...", "I guess", "I took" - first-person experience reports.
    "first_person_report": re.compile(
        r"\bi'?ve (been|had|noticed)\b|\bi (guess|feel|felt|noticed|took|woke)\b",
        re.I,
    ),
    # "it's throbbing", "it hurts", "it just started" - asserting own symptoms
    # (assertion forms only, so a clinician QUOTING "was it throbbing" is not
    # mistaken for the patient).
    "own_symptom": re.compile(
        r"\bit'?s (throbbing|worse|sore|itchy|painful|aching)\b"
        r"|\bit (hurts?|aches?|stings?|itches)\b"
        r"|\b(it|that) just (happened|started)\b"
        r"|\bmy \w+ (hurts?|aches?|started)\b",
        re.I,
    ),
    # "I've just got..." - patient opens by describing the presenting complaint.
    "presenting_complaint": re.compile(
        r"\bi'?ve (just )?(got|had) +(a |an )?"
        r"|\bi (just )?feel like\b"
        r"|\bi need to (vomit|throw up)\b",
        re.I,
    ),
}
# Doctor-direction cues precise enough to flip a row on their own (13/13,
# 3/3, and 3/4-with-question-mark on the baseline corpus); patient-direction
# single cues were coin flips and only earn uncertainty.
ROW_SINGLE_CUE_FLIPS = {
    "you_question",
    "clinical_question_mark",
    "clinician_self_intro",
    "consultation_opener",
}
ROW_SINGLE_CUE_FLIPS_WITH_QUESTION = {"second_person_body"}

# A '?' followed by a yes/no token means one row holds a question AND its
# answer (a seam blend); the answer tail is the freshest speaker.
_ROW_BLENDED_ANSWER = re.compile(r"\?\s*['\"]?\s*(no|yes|yeah|yep|nope)\b", re.I)
_ROW_SECOND_PERSON = re.compile(r"\byou(r|'re)?\b", re.I)
_ROW_POSSESSIVE_SELF = re.compile(r"\bmy\b", re.I)


def _row_cue_hits(text: str, cues: dict[str, re.Pattern[str]]) -> set[str]:
    """Return the names of cue rules matching one row's judged text.

    Args:
        text: Row text under judgment; empty matches nothing.
        cues: Doctor or patient cue rules.

    Returns:
        Matched rule names; empty means this side has no evidence in the row.
    """
    return {name for name, pattern in cues.items() if pattern.search(text)}


def summarize_role_establishment_cues(text: str) -> dict[str, int]:
    """Count role cues that help establish who is doctor or patient.

    Use this for speaker-level evidence sent to the role agent. Counts are
    evidence for the current visit, not final labels shown to the clinician.

    Args:
        text: Visible transcript row; empty means no role evidence.

    Returns:
        Cue counts for doctor-like, patient-like, and question-like wording.
    """
    doctor_hits = _row_cue_hits(text, ROW_DOCTOR_CUES)
    patient_hits = _row_cue_hits(text, ROW_PATIENT_CUES)

    return {
        "doctor": len(doctor_hits),
        "patient": len(patient_hits),
        "questions": int("?" in text),
    }


def _row_analysis_text(text: str) -> str:
    """Return the part of a row whose speaker the cues should judge.

    A mid-row question mark followed by a yes/no token blends a question and
    its answer in one visible row (a seam artifact). The answer tail is the
    freshest speaker, so cues judge the tail when it is substantial and stay
    with the asker when it is a token or two ("? No."). A '?' followed by the
    same speaker continuing ("...was it? For example...") is not a blend.

    Args:
        text: Full visible row text.

    Returns:
        Text to judge; empty means the row should keep its current label.
    """
    answer_match = _ROW_BLENDED_ANSWER.search(text)
    # No question+answer signature: judge the whole row.
    if not answer_match:
        return text

    answer_tail = text[answer_match.start() + 1 :].strip()
    # A token-sized answer tail means the question still owns the row's time.
    return answer_tail if len(answer_tail.split()) >= 3 else ""


def decide_row_role_exception(text: str, mapped_role: str) -> tuple[str, str]:
    """Judge one visible row's text against its speaker-mapped role.

    This is how a doctor question stuck inside a Patient card gets fixed (or
    honestly un-labeled) without relabeling the whole speaker.

    Args:
        text: Row text the clinician sees.
        mapped_role: Role the global speaker mapping gave this row.

    Returns:
        `(action, role)` where action is `keep` (no exception), `flip`
        (confident row relabel), or `uncertain` (row renders as UNKNOWN and
        counts as incorrect in strict attribution - never as a win).
    """
    # Rows without a confident mapped role have nothing to contradict.
    if mapped_role not in ("DOCTOR", "PATIENT"):
        return ("keep", mapped_role)

    # Short rows are seam-smear territory; leave them alone.
    if len(text.split()) < ROW_CUE_MIN_WORDS:
        return ("keep", mapped_role)

    judged_text = _row_analysis_text(text)
    # A question row with a token-sized answer tail stays with the asker.
    if judged_text == "":
        return ("keep", mapped_role)

    doctor_hits = _row_cue_hits(judged_text, ROW_DOCTOR_CUES)
    patient_hits = _row_cue_hits(judged_text, ROW_PATIENT_CUES)
    opposite_role = "PATIENT" if mapped_role == "DOCTOR" else "DOCTOR"
    same_hits = doctor_hits if mapped_role == "DOCTOR" else patient_hits
    opposite_hits = patient_hits if mapped_role == "DOCTOR" else doctor_hits

    # No one-sided contradiction: the row keeps its mapped role.
    if not opposite_hits:
        return ("keep", mapped_role)

    # Cue evidence for both speakers in one row is a blend: uncertain.
    if same_hits:
        return ("uncertain", "UNKNOWN")

    # Echo/quote guards: a patient echoing the clinician ("...my tummy") or a
    # clinician quoting symptoms back ("you said it hurts") are blends, not
    # confident contradictions.
    if mapped_role == "PATIENT" and _ROW_POSSESSIVE_SELF.search(judged_text):
        return ("uncertain", "UNKNOWN")
    if mapped_role == "DOCTOR" and _ROW_SECOND_PERSON.search(judged_text):
        return ("uncertain", "UNKNOWN")

    # Two independent contradicting cues always flip.
    if len(opposite_hits) >= ROW_CUE_MIN_FLIP_CUES:
        return ("flip", opposite_role)

    # Single-cue flips only for the rules measured reliable on their own.
    single_cue = next(iter(opposite_hits))
    if single_cue in ROW_SINGLE_CUE_FLIPS:
        return ("flip", opposite_role)
    if single_cue in ROW_SINGLE_CUE_FLIPS_WITH_QUESTION and "?" in text:
        return ("flip", opposite_role)

    return ("uncertain", "UNKNOWN")


def compute_row_role_exceptions(
    stored_segments: list[dict[str, Any]],
    mapping: dict[str, str],
) -> dict[str, str]:
    """Find rows whose text contradicts their speaker-mapped role.

    Runs after every speaker-mapping application, so the exceptions always
    describe the labels the clinician currently sees. Rows the clinician
    corrected themselves are authoritative and never re-judged.

    Args:
        stored_segments: Visible transcript rows from session storage.
        mapping: Current speaker-to-role mapping; empty judges nothing.

    Returns:
        `segment_id -> role` exceptions (role may be `UNKNOWN` for uncertain
        rows); empty means every row agrees with its mapped speaker role.
    """
    # Without an established mapping there is no confident label to contradict.
    if not mapping:
        return {}

    row_exceptions: dict[str, str] = {}
    # Every stored row is one line the clinician can currently read.
    for segment in stored_segments:
        segment_id = str(segment.get("segment_id", ""))

        # Rows without identity cannot be individually relabeled.
        if segment_id == "":
            continue

        # The clinician's own row corrections outrank automatic judgment.
        if segment.get("role_source") == "user_row":
            continue

        mapped_role = mapping.get(str(segment.get("speaker_id", "")), "")
        action, row_role = decide_row_role_exception(
            str(segment.get("text", "")), mapped_role
        )

        # Keep decisions mean the mapped role already fits this row.
        if action == "keep":
            continue

        row_exceptions[segment_id] = row_role

        # The bound keeps the roles payload sane on pathological sessions;
        # hitting it is loud so a capped session cannot read as fully judged.
        if len(row_exceptions) >= ROW_EXCEPTIONS_MAX:
            logger.warning(
                "row_exceptions.capped max=%s segments=%s",
                ROW_EXCEPTIONS_MAX,
                len(stored_segments),
                extra={
                    "max_row_exceptions": ROW_EXCEPTIONS_MAX,
                    "segments_considered": len(stored_segments),
                },
            )
            break

    return row_exceptions


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
