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

from corrected_role_cues import (
    normalize_corrected_role_phrase,
    role_from_corrected_phrase,
)

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
    # "got to say/ask" is clinician discourse ("I must say"), never a complaint,
    # so the infinitive form is excluded (consult 1.2 doctor remark).
    "presenting_complaint": re.compile(
        r"\bi'?ve (just )?(got|had) +(?!to\b)(a |an )?"
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
# A patient OFFERING information ("do you want to know more about it?") is
# question-shaped but role-neutral: both speakers offer detail this way. The
# stutter form "want to, to know" from the official audio is included.
_ROW_PATIENT_OFFER_FORM = re.compile(
    r"\b(?:do|would) you (?:want|like)(?:,?\s+to)+\s+(?:know|hear|see)\b", re.I
)
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

    # An information offer on a Patient row is not clinical interviewing; its
    # question-form cues (one question can fire both) are struck as Doctor
    # evidence so the offer cannot flip the patient's own card (consult 1.2).
    if mapped_role == "PATIENT" and _ROW_PATIENT_OFFER_FORM.search(judged_text):
        doctor_hits -= {"you_question", "clinical_question_mark"}

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

    _add_orphan_speaker_exceptions(stored_segments, mapping, row_exceptions)
    return row_exceptions


def _add_orphan_speaker_exceptions(
    stored_segments: list[dict[str, Any]],
    mapping: dict[str, str],
    row_exceptions: dict[str, str],
) -> None:
    """Relabel orphan-speaker rows whose reassembled phrases carry a clear cue.

    The streaming engine can mint an extra speaker ID before its voice cache
    settles; role mapping then gives that orphan a role some established
    speaker already holds, and the clinician sees the doctor's opening turns
    on Patient cards. This M11 lane judges ONLY those orphan rows, joining
    each with its same-speaker chronological neighbors because live rows
    fragment cue phrases mid-utterance ("...your name and age" / "please?").

    Args:
        stored_segments: Visible transcript rows from session storage.
        mapping: Current speaker-to-role mapping.
        row_exceptions: M20 lane output, updated in place; rows it already
            judged keep that decision.

    Returns:
        None; orphan-row relabels are added to `row_exceptions` in place.
    """
    orphan_speakers = orphan_speaker_ids(stored_segments, mapping)
    # Sessions whose mapped speakers hold distinct roles have no orphan lane.
    if not orphan_speakers:
        return

    ordered_rows = sorted(
        stored_segments,
        key=lambda row: (
            float(row.get("start", 0.0) or 0.0),
            float(row.get("end", 0.0) or 0.0),
        ),
    )
    # Each orphan row is judged with its immediate same-speaker neighbors only,
    # so a patient's one-word answer can never inherit the doctor's question cues.
    for row_position, segment in enumerate(ordered_rows):
        speaker_id = str(segment.get("speaker_id", ""))

        # Established-speaker rows belong to the measured M20 lane, not this one.
        if speaker_id not in orphan_speakers:
            continue

        segment_id = str(segment.get("segment_id", ""))
        # Unidentifiable rows and rows already judged keep their current state.
        if segment_id == "" or segment_id in row_exceptions:
            continue

        # The clinician's own row corrections outrank automatic judgment.
        if segment.get("role_source") == "user_row":
            continue

        mapped_role = mapping.get(speaker_id, "")
        # Orphans without a confident mapped role render raw; nothing to fix.
        if mapped_role not in ("DOCTOR", "PATIENT"):
            continue

        inferred_role = decide_orphan_row_role(
            str(segment.get("text", "")),
            _same_speaker_neighbor_text(ordered_rows, row_position, -1),
            _same_speaker_neighbor_text(ordered_rows, row_position, 1),
        )
        # No decisive cue, or agreement with the mapping, means no exception.
        if inferred_role is None or inferred_role == mapped_role:
            continue

        # The shared bound stays authoritative for the whole exceptions payload.
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
            return

        row_exceptions[segment_id] = inferred_role


def orphan_speaker_ids(
    stored_segments: list[dict[str, Any]],
    mapping: dict[str, str],
) -> set[str]:
    """Find speaker IDs whose mapped role a stronger speaker already owns.

    Row counts decide who owns a role: the speaker the clinician heard most
    under that role is canonical, and every same-role speaker with strictly
    fewer rows is an orphan (a pre-settle identity fragment). Ties orphan
    nobody, so an evenly split session is never judged by this lane.

    Args:
        stored_segments: Visible transcript rows from session storage.
        mapping: Current speaker-to-role mapping; empty means no orphans.

    Returns:
        Orphan speaker IDs; empty means every mapped role has one owner.
    """
    row_counts: dict[str, int] = {}
    # Only rows the clinician can see count toward role ownership.
    for segment in stored_segments:
        speaker_id = str(segment.get("speaker_id", ""))
        if speaker_id:
            row_counts[speaker_id] = row_counts.get(speaker_id, 0) + 1

    speakers_by_role: dict[str, list[str]] = {}
    # Group mapped speakers by the role the UI currently shows for them.
    for speaker_id, role in mapping.items():
        if speaker_id in row_counts and role in ("DOCTOR", "PATIENT"):
            speakers_by_role.setdefault(role, []).append(speaker_id)

    orphans: set[str] = set()
    # A role with several voices keeps its loudest voice and orphans the rest.
    for speakers in speakers_by_role.values():
        if len(speakers) < 2:
            continue

        top_count = max(row_counts[speaker] for speaker in speakers)
        for speaker in speakers:
            # Strictly fewer rows than the owner marks a pre-settle fragment.
            if row_counts[speaker] < top_count:
                orphans.add(speaker)

    return orphans


def decide_orphan_row_role(
    row_text: str,
    previous_same_speaker_text: str,
    next_same_speaker_text: str,
) -> str | None:
    """Judge one orphan row by its own text, then by same-speaker joins.

    Args:
        row_text: Orphan row text; empty means no evidence.
        previous_same_speaker_text: Immediate earlier neighbor's text when it
            shares the speaker ID; empty means no safe backward join exists.
        next_same_speaker_text: Immediate later neighbor's text when it shares
            the speaker ID; empty means no safe forward join exists.

    Returns:
        `DOCTOR`/`PATIENT` when exactly one reassembled phrase is decisive,
        or None when the row should keep its mapped role.
    """
    candidate_phrases = [row_text]
    # Fragmented cues complete backward ("...your name and age" + "please?").
    if previous_same_speaker_text:
        candidate_phrases.append(f"{previous_same_speaker_text} {row_text}")
    # And forward ("...how can I" + "help you this afternoon?").
    if next_same_speaker_text:
        candidate_phrases.append(f"{row_text} {next_same_speaker_text}")

    # The first decisive phrase wins; later joins cannot overrule direct text.
    for phrase in candidate_phrases:
        inferred_role = role_from_corrected_phrase(
            normalize_corrected_role_phrase(phrase)
        )
        if inferred_role is not None:
            return inferred_role

    return None


def _same_speaker_neighbor_text(
    ordered_rows: list[dict[str, Any]],
    row_position: int,
    direction: int,
) -> str:
    """Return the adjacent row's text only when it shares this row's speaker.

    Args:
        ordered_rows: Rows sorted chronologically.
        row_position: Index of the orphan row being judged.
        direction: `-1` for the previous neighbor, `1` for the next.

    Returns:
        Neighbor text, or empty when the neighbor belongs to another speaker -
        joining across a turn is how the M11 gate produced its one false flip.
    """
    neighbor_position = row_position + direction
    # Session edges have no neighbor to complete a fragmented phrase.
    if neighbor_position < 0 or neighbor_position >= len(ordered_rows):
        return ""

    neighbor = ordered_rows[neighbor_position]
    # A different voice next door means any shared phrase crosses a turn.
    if str(neighbor.get("speaker_id", "")) != str(
        ordered_rows[row_position].get("speaker_id", "")
    ):
        return ""

    return str(neighbor.get("text", ""))


def heuristic_role_inference(
    segments: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Infer DOCTOR/PATIENT labels from visible transcript text.

    Args:
        segments: Transcript segments shown in the UI; empty means no role evidence exists.

    Returns:
        Role mapping payload, or `None` when the UI should keep raw speaker labels.
    """
    # No per-speaker evidence means the browser should keep raw speaker labels.
    if not segments:
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
                # Extra speakers (family members, carers) have no keyword evidence;
                # guessing PATIENT would misattribute their words in the note, so
                # the UI keeps the raw speaker label instead.
                mapping[speaker_id] = "UNKNOWN"
