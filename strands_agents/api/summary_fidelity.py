"""Verify the generated note against the transcript before the clinician sees it.

When the user requests a summary, these checks catch unsupported uncertainty,
denials, exam claims, patient mental states, and non-verbatim quotations. A
failed draft gets one retry; surviving claims stay visible with a warning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Phrases a patient uses when they cannot answer a clinical question; a note
# sentence about that topic must keep this uncertainty visible.
_UNCERTAINTY_PHRASES = (
    "i don't know",
    "i dont know",
    "don't know really",
    "not sure",
    "i'm not sure",
    "can't remember",
    "cannot remember",
    "no idea",
    "not certain",
)

# Words a note may use to keep an unresolved point honestly unresolved.
_NOTE_UNCERTAINTY_MARKERS = (
    "unclear",
    "unsure",
    "uncertain",
    "unknown",
    "not established",
    "not stated",
    "did not know",
    "didn't know",
    "don't know",
    "does not know",
    "couldn't say",
    "could not say",
    "not certain",
    "patient-unsure",
)

# A note may attribute uncertainty, memory failure, lack of knowledge, or
# refusal to the patient only when the patient's own words establish that
# state for the same clinical topic.
_PATIENT_STATE_MARKER_PATTERN = re.compile(
    r"""
    (?P<memory>
        \b(?:
            (?:unable|inability)\s+to\s+(?:clearly\s+)?
             (?:recall|remember|specify|detail|name|identify)
            |(?:cannot|can\s+not|can't|could\s+not|couldn't|did\s+not|didn't
             |does\s+not|doesn't|do\s+not|don't)
             \s+(?:clearly\s+)?(?:recall|remember|specify|detail|name|identify)
            |not(?:\s+[a-z]+){0,3}\s+i\s+(?:recall|remember)
        )\b
    )
    |(?P<knowledge>
        \b(?:
            unsure|uncertain|not\s+sure
            |(?:did\s+not|didn't|does\s+not|doesn't|do\s+not|don't)\s+know
        )\b
    )
    |(?P<refusal>
        \b(?:
            (?:declined|refused)\s+to\s+(?:answer|say|name|state|disclose|discuss)
            |(?:would\s+rather\s+not|did\s+not\s+want\s+to|didn't\s+want\s+to)
             \s+(?:answer|say|name|state|disclose|discuss)
        )\b
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# The state must be explicitly attributed to the patient, including the
# gendered/neutral pronoun forms produced by the summary model.
_PATIENT_ATTRIBUTION_PATTERN = re.compile(
    r"\b(?:patient|she|he|they|her|his)\b", re.IGNORECASE
)

# Subjective notes often omit the repeated patient noun: after the user opens
# the note, "Takes an inhaler but cannot recall..." still reads as patient history.
_IMPLICIT_SUBJECTIVE_PATIENT_PATTERN = re.compile(
    r"^\s*(?:currently\s+)?(?:unable|takes?|uses?|reports?|describes?|states?|recalls?|"
    r"remembers?|has|had|feels?|felt|lives?|works?|attends?)\b",
    re.IGNORECASE,
)
_NON_PATIENT_STATE_ATTRIBUTION_PATTERN = re.compile(
    r"\b(?:doctor|clinician|physician|provider|transcript|record|audio)\b",
    re.IGNORECASE,
)

# Words surrounding a state marker that describe grammar rather than its
# clinical topic. The remaining words reuse the denial stem matcher.
_STATE_TOPIC_STOPWORDS = frozenset(
    "about answer answered answering as being could details develop developed developing "
    "develops did does due had has have he her incomplete insect lack reduced regarding "
    "specifically "
    "him his inability is it its kind knew know known medication-specific "
    "of one ones patient prior recall recalled recalling reaction-specific really remember "
    "remembered remembering say said she similar specifics state stated stating takes the "
    "their them they this to transcript unable unsure was were whether which who".split()
)

# Explicit quote attribution narrows evidence to that role. Unattributed
# quotation marks may match either speaker role.
_PATIENT_QUOTE_ATTRIBUTION_PATTERN = re.compile(r"\bpatient\b", re.IGNORECASE)
_DOCTOR_QUOTE_ATTRIBUTION_PATTERN = re.compile(
    r"\b(?:doctor|clinician|physician|provider|gp)\b", re.IGNORECASE
)
# Possessive role mentions describe ("the patient's presentation") rather than
# attribute - unless the possessed noun is the speech itself ("the patient's
# words"). Reproduced on the retained 3.1 replay note, where the possessive
# stole a clinician quote and rendered a wrong-role warning.
_QUOTE_POSSESSIVE_SPEECH_NOUNS = frozenset(
    {"words", "account", "description", "phrase", "phrasing", "report", "statement"}
)
# A quote may bridge ONE token-sized opposite-role interjection ("Okay.")
# because the speaker's own statement continued around it; anything longer, or
# a second interruption, is a real turn change. Reproduced on the retained
# day3-c01 note, where a one-token clinician backchannel split the patient's
# continuous quoted statement into a false warning.
_QUOTE_BACKCHANNEL_MAX_TOKENS = 3

# Clinical characteristics whose value must come from the patient, keyed by the
# words a doctor uses when asking. Each entry lists the assertion tokens a note
# would use to (wrongly) resolve the answer.
_CHARACTERISTIC_ASSERTION_TOKENS = {
    "onset": ("sudden", "suddenly", "gradual", "gradually", "abrupt", "abruptly"),
}

# Note phrasings that claim a negative finding the clinician can act on.
_NEGATIVE_FINDING_PATTERN = re.compile(
    r"\b(denies|denied|reports? no|no complaints? of|negative for|not experiencing|"
    r"does not report|without any|no history of)\b\s+(?P<topics>[^.;:]{3,240})",
    re.IGNORECASE,
)

# Contrast wording ends the denied list. The social-history form also stops at
# "and lives alone" so the user's home situation never becomes a denied topic.
_DENIAL_TOPIC_SCOPE_BOUNDARY_PATTERN = re.compile(
    r"\b(?:but|however|though|although|while)\b"
    r"|\band\s+(?:(?:the patient|patient|she|he|they)\s+)?"
    r"lives?\b",
    re.IGNORECASE,
)

# Patient words that count as an actual denial the note may rely on.
_PATIENT_DENIAL_PATTERN = re.compile(
    r"\b(no|not|nope|never|don'?t|doesn'?t|haven'?t|hasn'?t|none)\b", re.IGNORECASE
)

# Phrases that voice uncertainty or a non-answer. They contain negation words
# ("don't know") but prove the patient could NOT answer - never that they denied
# the topic - so they are masked out before any denial matching.
_EPISTEMIC_NON_ANSWER_PHRASES = (
    "don't know",
    "dont know",
    "do not know",
    "not sure",
    "unsure",
    "can't remember",
    "cant remember",
    "cannot remember",
    "can not remember",
    "don't think",
    "dont think",
    "do not think",
    "no idea",
)

# Local-clause boundaries inside one patient row: sentence punctuation plus the
# contrast/sequence words live ASR uses instead of punctuation. Commas and bare
# "and"/"or" deliberately do NOT split, so a coordinated denial list such as
# "no fever or rash" stays one clause and supports every listed topic.
_CLAUSE_BOUNDARY_PATTERN = re.compile(
    r"[.!?;]+|\bbut\b|\band then\b|\bthen\b|\bso\b", re.IGNORECASE
)

# A note sentence that claims a denial while admitting the question went
# unanswered contradicts itself; no transcript evidence can rescue it.
_UNANSWERED_ADMISSION_PATTERN = re.compile(
    r"\b(not (fully |completely )?answered|unanswered|no answer)\b", re.IGNORECASE
)

# The short-answer path only trusts an answer that OPENS with a denial word.
_ANSWER_DENIAL_OPENERS = frozenset({"no", "nope", "not", "never", "none"})

# Affirmative content disqualifies a short "denial" answer outright.
_AFFIRMATIVE_ANSWER_PATTERN = re.compile(r"\b(yes|yeah|yep)\b", re.IGNORECASE)

# A short denial answer covers at most this many words - beyond that it is a
# monologue whose topics must earn local clause support instead.
_SHORT_ANSWER_MAX_WORDS = 8

# A user may answer a multi-part normal-screen question with a short "all fine"
# rather than "no". The bounded wording inherits only the nearby question.
_SHORT_NORMAL_SCREEN_ANSWER_PATTERN = re.compile(
    r"^(?:(?:that'?s|that is|it'?s|it is|they(?:'re| are))\s+)?"
    r"(?:all\s+)?(?:fine|normal)[.!?,\s]*$",
    re.IGNORECASE,
)

# Corrected diarization can fold a short patient "No." onto the next clinician
# row. Only a new question or acknowledgement after the punctuation proves that shape.
_FOLDED_PATIENT_DENIAL_PATTERN = re.compile(
    r"^\s*(?P<answer>no|nope|none)\s*[,.!?]\s*(?P<continuation>.+)$",
    re.IGNORECASE,
)
_FOLDED_CLINICIAN_CONTINUATION_PATTERN = re.compile(
    r"^(?:okay|ok|all right|right|fine|any|do|did|does|have|has|is|are|can|"
    r"could|would|what|when|where|why|how)\b",
    re.IGNORECASE,
)

# The opposite row merge can put the clinician's question tail and the patient's
# short answer together, for example `history of that? No.` in c03.
_PATIENT_ROW_QUESTION_TAIL_DENIAL_PATTERN = re.compile(
    r"^(?P<question>.+\?)\s*(?P<answer>no|nope|none)[.!?,\s]*$",
    re.IGNORECASE,
)

# The locality window is six clinician fragments. Counting clinician rows,
# not every ASR card, keeps one split question together for the user's answer.
_RECENT_DENIAL_QUESTION_ROWS = 6

# Note language that claims an examination happened or produced findings.
_EXAM_CLAIM_PATTERNS = (
    re.compile(
        r"\b(examination|exam)\b[^.;]*\b(revealed|showed|demonstrated|initiated|"
        r"performed|completed|began)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(intact|normal on (examination|exam))\b", re.IGNORECASE),
    re.compile(
        r"\b(neurological|physical) examination\b[^.;]*\b(negative|normal|findings)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bvital signs\b[^.;]*\b(were|are)\s+(normal|stable)\b", re.IGNORECASE),
)

# Honest-absence frames stay unflagged - saying no exam happened IS fidelity.
# Beyond literal negation tokens, scribe-true framings such as "concludes
# before examination is performed", "prior to examination", and "not yet
# performed" also assert absence, not findings.
_EXAM_ABSENCE_PATTERN = re.compile(
    r"\b(no|not|none|without|absent)\b[^.;]*\b(documented|recorded|performed|"
    r"examined|captured|available|obtained|completed)\b"
    r"|\bnot (fully )?documented\b|\bno (formal )?(physical )?examination\b"
    r"|\b(conclude[sd]?|end(s|ed)?|stop(s|ped)?|finishe[sd])\b[^.;]*\bbefore\b"
    r"[^.;]*\b(examination|exam)\b"
    r"|\bprior to\b[^.;]*\b(examination|exam)\b"
    r"|\b(examination|exam)\b[^.;]*\bnot yet (performed|completed|done|carried out)\b",
    re.IGNORECASE,
)

# Note sentences where the examination is only proposed or planned - the exam
# noun sits under an intent verb, so no findings are being claimed (from
# the 2026-07-10 c03 replay false positive "Doctor proposed ... examination,
# with discussion to follow regarding findings").
_EXAM_INTENT_PATTERN = re.compile(
    r"\b(propose[sd]?|plan(?:s|ned)?|intend(?:s|ed)?|intention|will|would|"
    r"agreed|offered|advised|recommend(?:s|ed)?)\b[^.;]*\b(examination|exam|examine)\b",
    re.IGNORECASE,
)

# Transcript evidence that an examination was actually performed (not merely
# promised). The pilot fixtures contain none, so any positive exam claim flags.
_EXAM_PERFORMANCE_PATTERN = re.compile(
    r"\b(on examination|i can see|i can feel|i'?m listening to|reflexes are)\b",
    re.IGNORECASE,
)

# Note phrasing that presupposes an examination took place - even a negative
# finding ("no fever documented on examination") asserts the exam context.
_EXAM_PRESUPPOSITION_PATTERN = re.compile(r"\bon exam(?:ination)?\b", re.IGNORECASE)

# Small stem map so a note's clinical vocabulary matches the patient's words.
_TOPIC_STEM_SYNONYMS = {
    "dyspnea": ("breath",),
    "dyspnoea": ("breath",),
    "breathing": ("breath",),
    "breathless": ("breath",),
    "breathlessness": ("breath",),
    "depressed": ("depress", "down"),
    "diagnosis": ("diagnos", "told"),
    "fever": ("fever", "temperature", "hot"),
    "fevers": ("fever", "temperature", "hot"),
    "falls": ("fall",),
    "injury": ("injur",),
    "pyrexia": ("fever", "temperature"),
    "emesis": ("vomit", "sick"),
    "vomiting": ("vomit", "sick"),
    "nausea": ("nause", "sick"),
    # Headache-radiation paraphrases: notes write "spreading/radiating to other
    # locations", clinicians ask "is it moving anywhere else".
    "spreading": ("spread", "mov", "radiat"),
    "spread": ("spread", "mov", "radiat"),
    "smoking": ("smok",),
    "radiating": ("radiat", "mov", "spread"),
    "radiation": ("radiat", "mov", "spread"),
    "moving": ("mov", "spread", "radiat"),
    "location": ("locat", "anywhere", "elsewhere", "area"),
    "locations": ("locat", "anywhere", "elsewhere", "area"),
    "reflux": ("reflux", "throat"),
    "urinary": ("urinar", "urine"),
    "ability": ("abil", "able", "unable"),
    "laziness": ("lazy", "motivat"),
    "motivation": ("motivat", "lazy"),
    "tightness": ("tight",),
    "tobacco": ("tobacco", "smok"),
}

# Words too generic to identify a denied topic on their own.
_TOPIC_STOPWORDS = frozenset(
    "a an and or of in on at to the any some with for recent formal his her their "
    "do does did not "
    "difficulty difficulties problem problems issue issues history further other "
    "associated significant when patient own regular use screened screening neurological symptoms "
    "including denied denies negative reports reported states stated "
    "sensation sensations feeling feelings".split()
)

# Denial objects that name nothing checkable ("denied all") - the real topics
# then live earlier in the sentence, before the denial verb.
_VACUOUS_TOPIC_WORDS = frozenset(
    "all any everything each both them these those".split()
)

# Key-point shape "Neurological screening (speech, ..., head injury) negative"
# claims every listed item was denied - each item must be verified like a denial.
_SCREEN_NEGATIVE_PATTERN = re.compile(
    r"\bscreen(?:ing|ed)?\b[^.;:]*\bnegative\b", re.IGNORECASE
)


@dataclass(frozen=True)
class FidelityViolation:
    """
    One note sentence the transcript does not support.

    The browser shows the sentence with an "unverified against transcript"
    marker when regeneration cannot fix it, so the clinician never acts on it
    unknowingly.

    Attributes:
        location: `sections[i]` heading or `key_points` - where the reader sees it.
        sentence: The exact sentence to flag; never empty.
        rule: Stable rule id for logs and tests.
        reason: Plain-English explanation used in the regeneration prompt.
        subtype: Non-clinical failure classification for PHI-safe diagnostics
            (e.g. "no-local-topic-support"); empty only on legacy constructions.
        sentence_ordinal: Zero-based sentence position within its location; -1
            when the caller did not track ordinals.
        word_count: Lexical word count of the sentence; 0 when untracked.
    """

    location: str
    sentence: str
    rule: str
    reason: str
    subtype: str = ""
    sentence_ordinal: int = -1
    word_count: int = 0


@dataclass(frozen=True)
class _QuotedSpan:
    """Keep one paired quote and its position in the visible note sentence.

    The verifier uses it when deciding whether the clinician sees a verbatim quote.
    Each instance covers one quote, so a valid span cannot hide an invalid one.
    """

    text: str
    start: int
    end: int


def regeneration_feedback(violations: list[FidelityViolation]) -> str:
    """Build the correction block for the user's one allowed note retry.

    Each visible failure is named so the regenerated note addresses it directly.

    Args:
        violations: Failures from the first draft; never empty when a retry runs.

    Returns:
        Prompt block naming every violating sentence and its transcript problem.
    """
    lines = [
        "",
        "YOUR PREVIOUS DRAFT WAS REJECTED for asserting content the transcript does not",
        "support. Rewrite the note WITHOUT these fabrications:",
    ]
    # Each violation names the exact sentence so the redo cannot miss it.
    for violation in violations:
        lines.append(
            f'- In {violation.location}: "{violation.sentence}" - {violation.reason}.'
        )

    return "\n".join(lines)


def summary_with_unverified_flags(
    parsed_summary: dict[str, Any], violations: list[FidelityViolation]
) -> dict[str, Any]:
    """Mark sentences that still fail after the user's one note retry.

    The browser keeps each sentence visible with the unverified marker.

    Args:
        parsed_summary: Dumped summary payload about to be published; mutated copy is returned.
        violations: Surviving violations; empty returns the payload unchanged.

    Returns:
        Payload where each affected section carries `unverified` sentences and
        key points carry `unverified_key_points`; absent keys mean a fully
        verified note that renders exactly as before.
    """
    # A clean note ships untouched, so old sessions render byte-identically.
    if not violations:
        return parsed_summary

    # Each surviving failure must be placed where the clinician reads it.
    for violation in violations:
        placed = False
        # The sentence is flagged where the clinician will actually read it.
        for section in parsed_summary.get("sections", []):
            # A matching section receives the browser's visible warning marker.
            if violation.sentence in str(section.get("content", "")):
                section.setdefault("unverified", [])
                # One flag per sentence keeps repeated rules from stacking markers.
                if violation.sentence not in section["unverified"]:
                    section["unverified"].append(violation.sentence)
                placed = True
        # Key-point lines live outside sections and carry their own flag list.
        if not placed:
            # Each visible key point is another possible location for the sentence.
            for key_point in parsed_summary.get("key_points", []):
                # A matching key point needs the same warning as section prose.
                if violation.sentence in str(key_point):
                    flagged_points = parsed_summary.setdefault(
                        "unverified_key_points", []
                    )
                    # A duplicate rule must not create duplicate UI markers.
                    if key_point not in flagged_points:
                        flagged_points.append(key_point)

    return parsed_summary


def find_fidelity_violations(
    sections: list[dict[str, Any]],
    key_points: list[str],
    transcript_rows: list[dict[str, Any]],
) -> list[FidelityViolation]:
    """Check every generated note sentence against the visit transcript.

    After citation validation, results choose clean display, one retry, or a visible warning.

    Args:
        sections: Generated SOAP sections (heading/content); empty means a sparse note with nothing to check.
        key_points: TL;DR strip lines; empty means the strip is hidden and skipped.
        transcript_rows: Visit rows with role/text; empty means no evidence exists, so nothing can be verified
            and no violation is raised (the too-short-transcript rule already covers that case).

    Returns:
        Violations in reading order; empty means the note is fidelity-clean and ships as-is.
    """
    # With no transcript rows there is no evidence base; the short-transcript rule owns that case.
    if not transcript_rows:
        return []

    normalized_rows = [
        {
            "role": str(row.get("role", "")).upper(),
            "text": str(row.get("text", "") or "").lower(),
        }
        for row in transcript_rows
    ]
    uncertain_characteristics = _characteristics_answered_with_uncertainty(
        normalized_rows
    )

    violations: list[FidelityViolation] = []
    # Sections first, key points last - the same order the clinician reads.
    for section in sections:
        heading = str(section.get("heading", ""))
        # Every visible section sentence is verified independently.
        for ordinal, sentence in enumerate(_sentences(str(section.get("content", "")))):
            violations.extend(
                _sentence_violations(
                    sentence,
                    f"section '{heading}'",
                    normalized_rows,
                    uncertain_characteristics,
                    sentence_ordinal=ordinal,
                )
            )
    # Key points are verified after the full note sections the user reads first.
    for key_point in key_points:
        # A key point may contain more than one displayed sentence.
        for ordinal, sentence in enumerate(_sentences(str(key_point))):
            violations.extend(
                _sentence_violations(
                    sentence,
                    "key_points",
                    normalized_rows,
                    uncertain_characteristics,
                    sentence_ordinal=ordinal,
                )
            )

    return violations


def _contains_any_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    """Report whether any phrase appears as whole words in the text.

    Args:
        text: Lowercased sentence or transcript row; empty matches nothing.
        phrases: Whole-word phrases to look for.

    Returns:
        True when a phrase sits on word boundaries - "unsure" never matches "insured".
    """
    # Word boundaries keep clinical vocabulary from matching inside other words.
    for phrase in phrases:
        # One whole-phrase match is enough to support this wording.
        if re.search(rf"\b{re.escape(phrase)}\b", text) is not None:
            return True

    return False


def _sentences(text: str) -> list[str]:
    """Split note prose into the sentences the clinician reads.

    Args:
        text: Section content or one key point; empty produces no sentences to check.

    Returns:
        Non-empty sentences; empty list means there is nothing to verify here.
    """
    # A new sentence starts with a capital, digit, or quote - so "vs. gradual"
    # and "e.g. fever" never split a clinical phrase into a misleading fragment.
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'‘“])", text.strip())

    merged: list[str] = []
    # Each non-empty fragment becomes or extends one sentence shown in the note.
    for part in (piece.strip() for piece in parts if piece.strip()):
        # A fragment with an unclosed quote is mid-quotation - re-join it so
        # quoted patient speech is never judged as a bare fragment.
        if merged and _has_unbalanced_quotes(merged[-1]):
            merged[-1] = f"{merged[-1]} {part}"
        else:
            merged.append(part)

    return merged


def _has_unbalanced_quotes(sentence: str) -> bool:
    """Report whether a sentence fragment sits inside an unclosed quotation.

    Args:
        sentence: Candidate sentence from the splitter.

    Returns:
        True when a double quote or a boundary-anchored single quote is still
        open, meaning the "sentence" ends mid-quote and must be re-joined.
    """
    # Straight double quotes pair up; an odd count means the quote is open.
    if sentence.count('"') % 2 == 1:
        return True
    # Typographic quotes pair opener with closer.
    if sentence.count("“") != sentence.count("”"):
        return True
    # Single quotes count only at word boundaries, so apostrophes ("don't") stay out.
    boundary_singles = len(re.findall(r"(?:^|[\s(])'|'(?:[\s).,;:]|$)", sentence))
    return boundary_singles % 2 == 1


def _sentence_violations(
    sentence: str,
    location: str,
    normalized_rows: list[dict[str, str]],
    uncertain_characteristics: set[str],
    sentence_ordinal: int = -1,
) -> list[FidelityViolation]:
    """Run every deterministic fidelity rule over one note sentence.

    Args:
        sentence: One sentence the clinician would read; never empty here.
        location: Section heading or `key_points`, for the flag the user sees.
        normalized_rows: Lowercased transcript rows the sentence must be supported by.
        uncertain_characteristics: Characteristics the patient answered with "I don't know".
        sentence_ordinal: Zero-based position within the location, for PHI-safe logs.

    Returns:
        Violations for this sentence; empty means the sentence is supported.
    """
    sentence_violations: list[FidelityViolation] = []
    word_count = len(re.findall(r"[A-Za-z'][A-Za-z'-]*", sentence))

    def _add_visible_violation(rule: str, reason: str, subtype: str) -> None:
        """Add one warning for this sentence to the clinician-facing result.

        Use after a rule finds unsupported wording in the displayed note.
        """
        sentence_violations.append(
            FidelityViolation(
                location,
                sentence,
                rule,
                reason,
                subtype=subtype,
                sentence_ordinal=sentence_ordinal,
                word_count=word_count,
            )
        )

    unresolved = _uncertainty_violation(
        sentence, uncertain_characteristics, normalized_rows
    )
    # The patient said "I don't know", so a resolved value would mislead the reader.
    if unresolved is not None:
        _add_visible_violation("uncertainty-resolved", *unresolved)

    unsupported_denial = _negative_finding_violation(sentence, normalized_rows)
    # A denial the patient never gave reads as a cleared symptom to the clinician.
    if unsupported_denial is not None:
        _add_visible_violation("negative-without-denial", *unsupported_denial)

    exam_claim = _exam_language_violation(sentence, normalized_rows)
    # Exam findings that never happened carry the most clinical weight of all.
    if exam_claim is not None:
        _add_visible_violation("exam-not-performed", *exam_claim)

    invented_state = _patient_state_violation(sentence, location, normalized_rows)
    # Garbled or missing transcript material cannot become a patient mental state.
    if invented_state is not None:
        _add_visible_violation("patient-state-without-evidence", *invented_state)

    unsupported_quote = _non_verbatim_quote_violation(sentence, normalized_rows)
    # Quotation marks promise verbatim words, so every lexical span must verify.
    if unsupported_quote is not None:
        _add_visible_violation("non-verbatim-quote", *unsupported_quote)

    # The hedge lane catches only what the other rules missed: an already
    # flagged sentence keeps its one specific finding.
    if not sentence_violations:
        hedge_dropped = _hedged_statement_violation(sentence, normalized_rows)
        # A hedged answer ("Irregular, I think") cannot become a definite claim.
        if hedge_dropped is not None:
            _add_visible_violation("uncertainty-resolved", *hedge_dropped)

    return sentence_violations


def _characteristics_answered_with_uncertainty(
    normalized_rows: list[dict[str, str]],
) -> set[str]:
    """Find clinical characteristics the patient explicitly could not answer.

    Args:
        normalized_rows: Lowercased visit rows in spoken order; empty finds nothing.

    Returns:
        Characteristic names (e.g. "onset"); empty means no uncertainty exchange happened.
    """
    uncertain: set[str] = set()
    # Each doctor question is paired with the patient's answer up to two rows later.
    for row_index, row in enumerate(normalized_rows):
        # Patient rows cannot introduce the clinician's offered answer choices.
        if row["role"] != "DOCTOR":
            continue
        # Each clinical characteristic has its own supported answer vocabulary.
        for characteristic, tokens in _CHARACTERISTIC_ASSERTION_TOKENS.items():
            # The doctor must actually have offered the characteristic's options.
            if not any(token in row["text"] for token in tokens):
                continue
            # Nearby patient rows may contain the answer the note must preserve.
            for answer in normalized_rows[row_index + 1 : row_index + 3]:
                # Only the patient's own uncertainty makes the value unresolvable.
                if answer["role"] == "PATIENT" and _contains_any_phrase(
                    answer["text"], _UNCERTAINTY_PHRASES
                ):
                    uncertain.add(characteristic)

    return uncertain


def _uncertainty_violation(
    sentence: str,
    uncertain_characteristics: set[str],
    normalized_rows: list[dict[str, str]],
) -> tuple[str, str] | None:
    """Check that an unanswerable characteristic stays unresolved in the note.

    Args:
        sentence: Note sentence under review.
        uncertain_characteristics: Characteristics the patient answered with uncertainty.
        normalized_rows: Lowercased transcript rows, used to accept verbatim patient quotes.

    Returns:
        (reason, subtype), or None when the sentence is honest about the uncertainty.
    """
    lowered = sentence.lower()
    # Each unresolved characteristic must stay unresolved wherever it appears.
    for characteristic in uncertain_characteristics:
        tokens = _CHARACTERISTIC_ASSERTION_TOKENS[characteristic]
        # Sentences that never mention the characteristic cannot resolve it.
        if not any(token in lowered for token in tokens):
            continue

        token_pattern = "|".join(tokens)
        # "Onset was sudden" resolves the answer no matter what hedging follows.
        if re.search(rf"\b(was|is|appears?|seemed?)\s+({token_pattern})\b", lowered):
            return (
                f"the patient answered the {characteristic} question with uncertainty, but this"
                " sentence resolves it to a definitive value",
                "resolved-to-definitive",
            )
        # "Described as sudden" invents an attribution nobody made.
        if re.search(rf"\bdescribed as\s+({token_pattern})\b", lowered):
            return (
                f"nobody described the {characteristic} that way - the patient expressed"
                " uncertainty",
                "invented-attribution",
            )
        has_marker = _contains_any_phrase(lowered, _NOTE_UNCERTAINTY_MARKERS)
        quotes_patient = _has_patient_verbatim_quote(sentence, normalized_rows)
        # Mentioning the characteristic without honesty about the unknown misleads.
        if not has_marker and not quotes_patient:
            return (
                f"this sentence discusses {characteristic} without preserving the patient's"
                " stated uncertainty",
                "uncertainty-not-preserved",
            )

    # No unresolved characteristic was misstated, so the UI needs no warning.
    return None


def _normalized_lexical_tokens(text: str) -> tuple[str, ...]:
    """Normalize note/transcript words for quote comparison.

    Use before deciding whether a quoted phrase can stay unflagged in the note.
    """
    return tuple(re.findall(r"[^\W_]+", text.casefold()))


def _is_word_apostrophe(text: str, index: int) -> bool:
    """Separate apostrophes from quote marks in visible note text.

    Use so contractions such as "don't" never create a false UI warning.
    """
    # A mark at either edge cannot sit inside a word the clinician reads.
    if index <= 0 or index >= len(text) - 1:
        return False

    return text[index - 1].isalnum() and text[index + 1].isalnum()


def _extract_quoted_spans(sentence: str) -> list[_QuotedSpan]:
    """Find paired quote marks in one clinician-visible sentence.

    Use to verify lexical quotes while ignoring apostrophes and unpaired marks.
    """
    quoted_spans: list[_QuotedSpan] = []
    # Empty opener positions mean no quoted phrase is currently open in the note.
    straight_double_start: int | None = None
    straight_single_start: int | None = None
    curly_double_start: int | None = None
    curly_single_start: int | None = None

    def _save_closed_quote(opening_index: int | None, closing_index: int) -> None:
        """Save one paired quote for the note's final fidelity check.

        Use when the parser reaches the matching closing mark shown in the UI.
        """
        # A closing mark without an opener gives the user no complete quote to verify.
        if opening_index is None:
            return
        quoted_text = sentence[opening_index + 1 : closing_index]
        # Punctuation-only pairs make no verbatim lexical claim.
        if _normalized_lexical_tokens(quoted_text):
            quoted_spans.append(
                _QuotedSpan(quoted_text, opening_index, closing_index + 1)
            )

    # Each character may open/close one of the four quote styles shown in the note.
    for index, character in enumerate(sentence):
        # Straight double marks toggle one visible quoted phrase.
        if character == '"':
            # An empty start means the user is seeing the opening mark.
            if straight_double_start is None:
                straight_double_start = index
            else:
                _save_closed_quote(straight_double_start, index)
                straight_double_start = None
        # Standalone straight singles are quotes; word-internal singles are apostrophes.
        elif character == "'" and not _is_word_apostrophe(sentence, index):
            # An empty start means the user is seeing the opening mark.
            if straight_single_start is None:
                straight_single_start = index
            else:
                _save_closed_quote(straight_single_start, index)
                straight_single_start = None
        # A curly double opener starts the phrase shown to the clinician.
        elif character == "“":
            curly_double_start = index
        # A curly double closer saves the complete phrase, if one was opened.
        elif character == "”":
            _save_closed_quote(curly_double_start, index)
            curly_double_start = None
        # A curly single opener starts the phrase shown to the clinician.
        elif character == "‘":
            curly_single_start = index
        # A standalone curly closer ends a quote; word-internal marks stay apostrophes.
        elif character == "’" and not _is_word_apostrophe(sentence, index):
            _save_closed_quote(curly_single_start, index)
            curly_single_start = None

    return sorted(quoted_spans, key=lambda quoted_span: quoted_span.start)


def _transcript_role_token_sequences(
    normalized_rows: list[dict[str, str]],
) -> list[tuple[str, tuple[str, ...]]]:
    """Build same-speaker word runs from the transcript shown beside the note.

    Use so a genuine quote split across adjacent UI rows still verifies, and a
    statement continuing around a token-sized backchannel stays one run. Runs
    never contain another speaker's words; a substantive turn always closes.
    """
    roles_in_order: list[str] = []
    # Every distinct visible role owns its own bridged runs.
    for row in normalized_rows:
        if row["role"] not in roles_in_order:
            roles_in_order.append(row["role"])

    role_token_sequences: list[tuple[str, tuple[str, ...]]] = []
    for transcript_role in roles_in_order:
        role_token_sequences.extend(
            (transcript_role, token_run)
            for token_run in _bridged_role_token_runs(normalized_rows, transcript_role)
        )

    return role_token_sequences


def _bridged_role_token_runs(
    normalized_rows: list[dict[str, str]],
    role: str,
) -> list[tuple[str, ...]]:
    """Collect one speaker's word runs, bridging a single tiny interjection.

    A backchannel ("Okay.") between two rows of the same speaker does not end
    that speaker's statement, so the run continues WITHOUT the interjection's
    words. A second interruption, or one longer than the token bound, closes
    the run - that is a real turn change, never quote-joinable.

    Args:
        normalized_rows: Lowercased transcript rows in visible order.
        role: Speaker role whose runs are collected.

    Returns:
        Token runs for that speaker; empty when the speaker never spoke.
    """
    token_runs: list[tuple[str, ...]] = []
    current_tokens: list[str] = []
    bridged_interruptions = 0

    for row in normalized_rows:
        row_tokens = _normalized_lexical_tokens(row["text"])
        # Wordless rows carry no speech to join or to interrupt with.
        if not row_tokens:
            continue

        if row["role"] == role:
            current_tokens.extend(row_tokens)
            bridged_interruptions = 0
            continue

        # Nothing to bridge before the speaker has said anything.
        if not current_tokens:
            continue

        # One token-sized interjection keeps the statement open.
        if (
            len(row_tokens) <= _QUOTE_BACKCHANNEL_MAX_TOKENS
            and bridged_interruptions == 0
        ):
            bridged_interruptions = 1
            continue

        token_runs.append(tuple(current_tokens))
        current_tokens = []
        bridged_interruptions = 0

    # The final open run has no later turn change to flush it.
    if current_tokens:
        token_runs.append(tuple(current_tokens))

    return token_runs


def _contains_token_sequence(
    transcript_tokens: tuple[str, ...], quoted_phrase_tokens: tuple[str, ...]
) -> bool:
    """Check whether a displayed quote is one exact transcript word run.

    Use after punctuation/case normalization; an empty quote never verifies.
    """
    # An empty or overlong quote cannot be present in this transcript run.
    if not quoted_phrase_tokens or len(quoted_phrase_tokens) > len(transcript_tokens):
        return False

    quoted_word_count = len(quoted_phrase_tokens)
    # Each possible start position is compared with the complete quoted phrase.
    return any(
        transcript_tokens[index : index + quoted_word_count] == quoted_phrase_tokens
        for index in range(len(transcript_tokens) - quoted_word_count + 1)
    )


def _role_mention_attributes_speech(sentence: str, mention_end: int) -> bool:
    """Decide whether one role mention before a quote is a speaker attribution.

    Use because "based on the patient's clinical presentation, stating ..."
    describes the patient without giving them the quote.

    Args:
        sentence: Note text being scanned for attributions.
        mention_end: End index of the matched role word.

    Returns:
        True for plain mentions and for possessives that own the speech
        itself; False for descriptive possessives that must not attribute.
    """
    following_text = sentence[mention_end:]
    # Non-possessive mentions keep the historical last-mention behavior.
    if not (following_text.startswith("'s") or following_text.startswith("’s")):
        return True

    lookahead_words = re.findall(r"[a-z]+", following_text[2:].lower())[:3]
    return any(word in _QUOTE_POSSESSIVE_SPEECH_NOUNS for word in lookahead_words)


def _quote_attributed_role(sentence: str, quote_start: int) -> str | None:
    """Find which speaker the note explicitly credits with a quote.

    Use so the UI cannot present patient words as a clinician quotation, or vice versa.
    """
    note_text_before_quote = sentence[:quote_start]
    role_attributions: list[tuple[int, str]] = []
    # Patient mentions before the quote are candidate attributions.
    role_attributions.extend(
        (match.start(), "PATIENT")
        for match in _PATIENT_QUOTE_ATTRIBUTION_PATTERN.finditer(note_text_before_quote)
        if _role_mention_attributes_speech(note_text_before_quote, match.end())
    )
    # Clinician mentions before the quote are candidate attributions.
    role_attributions.extend(
        (match.start(), "DOCTOR")
        for match in _DOCTOR_QUOTE_ATTRIBUTION_PATTERN.finditer(note_text_before_quote)
        if _role_mention_attributes_speech(note_text_before_quote, match.end())
    )
    # No named speaker lets the quote match either role in the transcript UI.
    if not role_attributions:
        return None

    return max(role_attributions, key=lambda attribution: attribution[0])[1]


def _quote_span_supported(
    span: _QuotedSpan,
    normalized_rows: list[dict[str, str]],
    required_role: str | None,
) -> bool:
    """Verify one displayed quote against an allowed transcript speaker.

    Use before the browser decides whether that quote needs an unverified marker.
    """
    quoted_phrase_tokens = _normalized_lexical_tokens(span.text)
    # Each same-role transcript run is a possible source for the visible quote.
    for transcript_role, transcript_tokens in _transcript_role_token_sequences(
        normalized_rows
    ):
        # An attributed quote cannot borrow identical words from the other speaker.
        if required_role is not None and transcript_role != required_role:
            continue
        # One exact run is enough to keep this quote unflagged in the note.
        if _contains_token_sequence(transcript_tokens, quoted_phrase_tokens):
            return True

    return False


def _has_patient_verbatim_quote(
    sentence: str, normalized_rows: list[dict[str, str]]
) -> bool:
    """Check whether the note repeats any patient words verbatim.

    Use by the older uncertainty rule through the same quote mechanism the UI now trusts.
    """
    # Any exact patient quote preserves the older uncertainty wording safely.
    return any(
        _quote_span_supported(span, normalized_rows, "PATIENT")
        for span in _extract_quoted_spans(sentence)
    )


def _non_verbatim_quote_violation(
    sentence: str, normalized_rows: list[dict[str, str]]
) -> tuple[str, str] | None:
    """Find the first quote the transcript cannot support.

    Use before publishing; any failure makes the full visible sentence unverified.
    """
    # Every quote must independently earn its place in the clinician-facing note.
    for quoted_span in _extract_quoted_spans(sentence):
        required_role = _quote_attributed_role(sentence, quoted_span.start)
        # A supported quote needs no visible warning.
        if _quote_span_supported(quoted_span, normalized_rows, required_role):
            continue

        # Matching words from the wrong speaker still mislead the clinician.
        if required_role is not None and _quote_span_supported(
            quoted_span, normalized_rows, None
        ):
            return (
                "the quoted wording appears in the transcript, but not in the words of the"
                " speaker this sentence attributes it to",
                "quote-in-wrong-role",
            )
        return (
            "quotation marks must contain an exact contiguous phrase from the transcript;"
            " paraphrases and inferred names must remain unquoted",
            "quote-not-contiguous",
        )

    # No paired quote failed, so the sentence needs no quotation warning in the UI.
    return None


def _patient_state_kind(match: re.Match[str]) -> str:
    """Classify the patient state wording found in note or transcript text.

    Use so only the same state family can justify what the clinician reads.
    """
    # Each named group represents one distinct claim shown in the note.
    for patient_state_kind in ("memory", "knowledge", "refusal"):
        # The first populated group is the state this wording actually claims.
        if match.group(patient_state_kind) is not None:
            return patient_state_kind
    # This signals a developer pattern error, not a user-generated note failure.
    raise ValueError("patient-state marker match has no named family")


def _patient_state_topic(sentence: str, marker: re.Match[str]) -> str:
    """Extract the clinical topic attached to one displayed patient state.

    Use to prevent uncertainty about one symptom from supporting another.
    """
    trailing_clause = _CLAUSE_BOUNDARY_PATTERN.split(
        sentence[marker.end() :], maxsplit=1
    )[0]
    note_text_before_state = sentence[: marker.start()]
    topic_before_state = re.search(
        r"\basked\s+about\s+([^,;:.]+)", note_text_before_state, re.IGNORECASE
    )
    topic_clause = trailing_clause
    # A note may name what the clinician asked before saying the patient was uncertain.
    if topic_before_state is not None:
        topic_clause = topic_before_state.group(1)
    # Grammar words are removed so the remaining terms match transcript evidence.
    clinical_topic_words = [
        word
        for word in re.findall(r"[a-z][a-z-]+", topic_clause.lower())
        if word not in _TOPIC_STOPWORDS and word not in _STATE_TOPIC_STOPWORDS
    ]
    # The first bounded topic is enough for local evidence and avoids borrowing
    # unrelated clinical words later in a long generated sentence.
    return " ".join(clinical_topic_words[:8])


def _state_marker_inside_patient_quote(
    sentence: str,
    marker: re.Match[str],
    normalized_rows: list[dict[str, str]],
) -> bool:
    """Check whether the displayed state is inside a verified patient quote.

    Use so a clinician can safely see the patient's exact "I don't know" wording.
    """
    # Each visible quote is checked to see whether it contains this state marker.
    for quoted_span in _extract_quoted_spans(sentence):
        # A marker inside the paired delimiters inherits that quote's evidence.
        if quoted_span.start < marker.start() and marker.end() < quoted_span.end:
            return _quote_span_supported(quoted_span, normalized_rows, "PATIENT")

    return False


def _state_marker_local_window(row_text: str, marker: re.Match[str]) -> str:
    """Keep patient-state evidence close to its transcript wording.

    Use so an unrelated "I don't know" elsewhere in a long UI row cannot verify it.
    """
    clause_start = 0
    clause_end = len(row_text)
    # Clause boundaries keep distant symptoms from lending evidence to this state.
    for boundary in _CLAUSE_BOUNDARY_PATTERN.finditer(row_text):
        # Earlier boundaries move the local evidence start forward.
        if boundary.end() <= marker.start():
            clause_start = boundary.end()
        # The first later boundary closes the local evidence window.
        elif boundary.start() >= marker.end():
            clause_end = boundary.start()
            break

    return row_text[
        max(clause_start, marker.start() - 80) : min(clause_end, marker.end() + 120)
    ]


def _text_supports_topic(evidence_text: str, clinical_topic: str) -> bool:
    """Check whether local transcript text names the note's clinical topic.

    Use for patient words and the clinician question immediately above them.
    """
    # A generic state claim has no narrower topic to match.
    if clinical_topic == "":
        return True

    # One local clinical stem is enough here: state-family and clause/question
    # gates already prevent an unrelated "I don't know" from lending support.
    return (
        clinical_topic in evidence_text
        or _matched_topic_words(evidence_text, clinical_topic) >= 1
    )


def _trailing_question_is_unanswered(
    clinical_topic: str, normalized_rows: list[dict[str, str]]
) -> bool:
    """Find a clinician topic asked after the final patient response.

    Use so a visit ending mid-question never becomes patient uncertainty in the note.
    """
    # No extracted topic means there is no specific trailing question to compare.
    if clinical_topic == "":
        return False
    # The last patient row marks where unanswered clinician-only tail text begins.
    last_patient_index = max(
        (
            index
            for index, row in enumerate(normalized_rows)
            if row["role"] == "PATIENT"
        ),
        default=-1,
    )
    # Any matching clinician-only tail means the user never supplied an answer.
    return any(
        row["role"] == "DOCTOR" and _text_supports_topic(row["text"], clinical_topic)
        for row in normalized_rows[last_patient_index + 1 :]
    )


def _recent_clinician_question_context(
    patient_turn_start: int, normalized_rows: list[dict[str, str]]
) -> str:
    """Join bounded clinician fragments leading into the patient's answer.

    Use when the transcript view split one question/answer exchange into rows.
    """
    recent_rows = normalized_rows[max(0, patient_turn_start - 12) : patient_turn_start]
    # Only clinician words can supply the question inherited by a short answer.
    return " ".join(row["text"] for row in recent_rows if row["role"] == "DOCTOR")


def _adjacent_patient_state_runs(
    normalized_rows: list[dict[str, str]],
) -> list[tuple[int, str]]:
    """Join adjacent patient rows so one visible answer stays one evidence turn.

    Use when ASR split "I don't know how / effective it's been" across cards.
    """
    patient_runs: list[tuple[int, str]] = []
    # A null start means no patient answer is currently being joined.
    patient_turn_start: int | None = None
    patient_turn_text: list[str] = []
    # Each row either extends the current patient answer or flushes it at a role change.
    for row_index, row in enumerate(normalized_rows):
        # Adjacent patient rows render as one answer despite their ASR boundaries.
        if row["role"] == "PATIENT":
            # A null start means this is the first row in the visible patient turn.
            if patient_turn_start is None:
                patient_turn_start = row_index
            patient_turn_text.append(row["text"])
            continue
        # A non-null start means a speaker change just completed the patient turn.
        if patient_turn_start is not None:
            patient_runs.append((patient_turn_start, " ".join(patient_turn_text)))
            patient_turn_start = None
            patient_turn_text = []
    # A non-null final start means the transcript ended on the patient's answer.
    if patient_turn_start is not None:
        patient_runs.append((patient_turn_start, " ".join(patient_turn_text)))

    return patient_runs


def _patient_state_has_support(
    patient_state_kind: str,
    clinical_topic: str,
    normalized_rows: list[dict[str, str]],
) -> bool:
    """Match the note's patient state to the patient's topic-local words.

    Use before deciding whether the clinician sees a warning on that sentence.
    """
    # Each visible patient turn is an independent evidence candidate.
    for patient_turn_start, patient_turn_text in _adjacent_patient_state_runs(
        normalized_rows
    ):
        # One turn may contain several uncertainty or memory phrases.
        for evidence_marker in _PATIENT_STATE_MARKER_PATTERN.finditer(
            patient_turn_text
        ):
            # A memory claim cannot borrow support from a different state family.
            if _patient_state_kind(evidence_marker) != patient_state_kind:
                continue
            local_window = _state_marker_local_window(
                patient_turn_text, evidence_marker
            )
            # Topic words in the same local turn directly verify the displayed state.
            if _text_supports_topic(local_window, clinical_topic):
                return True
            # A fragmented "not that I recall" answer inherits the bounded
            # clinician question the user sees immediately above that turn.
            if _text_supports_topic(
                _recent_clinician_question_context(patient_turn_start, normalized_rows),
                clinical_topic,
            ):
                return True

    return False


def _state_is_attributed_to_patient(
    sentence: str, marker: re.Match[str], location: str
) -> bool:
    """Identify explicit or Subjective-implied patient state wording.

    Use for the note block the clinician reads; record/clinician wording stays exempt.
    """
    note_text_before_state = sentence[: marker.start()]
    patient_mentions = list(
        _PATIENT_ATTRIBUTION_PATTERN.finditer(note_text_before_state)
    )
    non_patient_mentions = list(
        _NON_PATIENT_STATE_ATTRIBUTION_PATTERN.finditer(note_text_before_state)
    )
    # An explicit patient/pronoun mention attributes the state unless a nearer source replaces it.
    if patient_mentions:
        latest_patient = patient_mentions[-1].start()
        # No non-patient mention means the patient remains the nearest source.
        latest_non_patient = (
            non_patient_mentions[-1].start() if non_patient_mentions else -1
        )
        return latest_patient > latest_non_patient
    # Subjective action sentences may omit "patient" while still reading as patient history.
    if location == "section 'Subjective'":
        # Naming the record/transcript makes "unable" a capture limitation, not cognition.
        if _NON_PATIENT_STATE_ATTRIBUTION_PATTERN.search(sentence) is not None:
            return False
        return _IMPLICIT_SUBJECTIVE_PATIENT_PATTERN.search(sentence) is not None

    return False


def _patient_state_violation(
    sentence: str,
    location: str,
    normalized_rows: list[dict[str, str]],
) -> tuple[str, str] | None:
    """Find a displayed patient state unsupported by the patient's words.

    Use before publishing so record gaps receive a visible fidelity warning.
    """
    # Every state phrase in the sentence must independently match patient evidence.
    for marker in _PATIENT_STATE_MARKER_PATTERN.finditer(sentence):
        # Clinician/record uncertainty is not a claim about the patient.
        if not _state_is_attributed_to_patient(sentence, marker, location):
            continue
        # An exact patient quote is already verified by the shared quote mechanism.
        if _state_marker_inside_patient_quote(sentence, marker, normalized_rows):
            continue

        patient_state_kind = _patient_state_kind(marker)
        clinical_topic = _patient_state_topic(sentence, marker)
        # A trailing clinician-only question can never establish a patient state.
        if _trailing_question_is_unanswered(clinical_topic, normalized_rows):
            return (
                "the note attributes a patient state to a clinician question that has no"
                " patient response",
                "trailing-question-unanswered",
            )
        # Missing topic-local patient evidence keeps the sentence visibly unverified.
        if not _patient_state_has_support(
            patient_state_kind, clinical_topic, normalized_rows
        ):
            return (
                "the sentence attributes uncertainty, memory failure, lack of knowledge, or"
                " refusal to the patient without local patient evidence for the same topic",
                "no-local-patient-state-support",
            )

    # Every displayed state is supported or record-scoped, so no UI warning is needed.
    return None


def _negative_finding_violation(
    sentence: str, normalized_rows: list[dict[str, str]]
) -> tuple[str, str] | None:
    """Check that every denied topic in the sentence has a real patient denial.

    Args:
        sentence: Note sentence possibly claiming denials.
        normalized_rows: Lowercased transcript rows searched for the patient's denial.

    Returns:
        (reason naming the unsupported topic, subtype), or None when all denials are real.
    """
    topics = _claimed_denial_topics(sentence)
    # Sentences with no denial language have nothing to prove.
    if not topics:
        return None

    # A sentence admitting its own question was never answered cannot also
    # claim the denial - no stem match in the transcript rescues it.
    if _UNANSWERED_ADMISSION_PATTERN.search(sentence) is not None:
        return (
            "the transcript contains no patient denial of this - the sentence itself admits"
            " the question was not answered, and an unanswered clinician question is not"
            " a denial",
            "contradicts-unanswered-admission",
        )

    # Every claimed negative finding needs its own patient evidence.
    for topic in topics:
        # Every denied topic needs the patient's own "no" somewhere near it.
        if not _did_patient_deny_topic(topic, normalized_rows):
            return (
                f"the transcript contains no patient denial of '{topic}' - an unanswered"
                " clinician question is not a denial",
                "no-local-topic-support",
            )

    # Every claimed denial has evidence, so the note stays unmarked.
    return None


def _claimed_denial_topics(sentence: str) -> list[str]:
    """Extract every topic a generated note claims the patient denied.

    Covers standard lists, `denied all`, and parenthetical negative screening text.

    Args:
        sentence: Note sentence under review.

    Returns:
        Cleaned topic phrases to verify; empty means the sentence claims no denial.
    """
    match = _NEGATIVE_FINDING_PATTERN.search(sentence)
    # A standard denial phrase exposes its topic text after the denial verb.
    if match is not None:
        denied_topic_text = match.group("topics")
        scope_boundary = _DENIAL_TOPIC_SCOPE_BOUNDARY_PATTERN.search(denied_topic_text)
        # Reported symptoms after "but" remain positive facts in the visible note.
        if scope_boundary is not None:
            denied_topic_text = denied_topic_text[: scope_boundary.start()]
        topics = _denied_topics(denied_topic_text)
        # "Denied all" names nothing - the actual topics sit before the verb.
        if topics and not all(topic in _VACUOUS_TOPIC_WORDS for topic in topics):
            return topics
        return _denied_topics(sentence[: match.start()])

    # "Screening (speech, ..., head injury) negative" claims each listed denial.
    if _SCREEN_NEGATIVE_PATTERN.search(sentence) is not None:
        parenthetical = re.search(r"\(([^)]{3,160})\)", sentence)
        listed = parenthetical.group(1) if parenthetical is not None else sentence
        return _denied_topics(listed)

    # No denial shape means the sentence gives the UI no negative topics to verify.
    return []


def _denied_topics(topics_text: str) -> list[str]:
    """Split a denial clause into individual denied topics.

    Args:
        topics_text: Text after the denial verb, e.g. "fever, weakness, or difficulty breathing";
            empty produces no topics and therefore no violation.

    Returns:
        Cleaned topic phrases; empty means the clause named nothing checkable.
    """
    raw_topics = re.split(r",|\bor\b|\band\b|/", topics_text)
    topics: list[str] = []
    # Each fragment keeps only the words that actually identify a symptom.
    for raw_topic in raw_topics:
        words = [
            word
            for word in re.findall(r"[a-z][a-z-]+", raw_topic.lower())
            if word not in _TOPIC_STOPWORDS
        ]
        # Empty fragments name no symptom and create no warning for the user.
        if words:
            topics.append(" ".join(words))

    return topics


def _mask_epistemic_phrases(row_text: str) -> str:
    """Blank out uncertainty phrases so their negation words cannot fake a denial.

    Args:
        row_text: Lowercased patient row; empty passes through unchanged.

    Returns:
        The row with each epistemic phrase replaced by a space - "i don't know
        what to do" keeps no denial token, while "i don't have a rash" does.
    """
    masked = row_text
    # Each uncertainty phrase is removed before searching for a real denial.
    for phrase in _EPISTEMIC_NON_ANSWER_PHRASES:
        masked = re.sub(rf"\b{re.escape(phrase)}\b", " ", masked)

    return masked


def _denial_evidence_clauses(masked_row_text: str) -> list[str]:
    """Split one patient row into the local clauses denial evidence may live in.

    Args:
        masked_row_text: Patient row with epistemic phrases already masked.

    Returns:
        Non-empty clauses; a denial and its topic must share one of these, so
        "my lips are swelling ... but ... no" never assembles into fake support.
    """
    clauses = _CLAUSE_BOUNDARY_PATTERN.split(masked_row_text)
    return [clause.strip() for clause in clauses if clause and clause.strip()]


def _matched_topic_words(text: str, topic: str) -> int:
    """Count how many of a topic's words the text mentions as word starts.

    Args:
        text: Lowercased clause or clinician row.
        topic: Cleaned denied-topic phrase such as "skin rashes".

    Returns:
        Number of topic words with a stem match; synonyms count for their word,
        so "breathless" matches the topic word "breathing".
    """
    matched = 0
    # Each topic word contributes at most one unit of transcript support.
    for word in topic.split():
        stems = _TOPIC_STEM_SYNONYMS.get(word, (word[:6],))
        # Word-start matching keeps short stems from finding phantom support.
        if any(re.search(rf"\b{re.escape(stem)}", text) for stem in stems):
            matched += 1

    return matched


def _topic_support_requirement(topic: str) -> int:
    """Report how many topic words a clause/question must match to count.

    Args:
        topic: Cleaned denied-topic phrase.

    Returns:
        2 for multi-word topics, 1 for single words - one chief-complaint word
        can never verify a compound claim like "prior lip swelling after food".
    """
    return min(2, len(topic.split()))


def _is_short_denial_answer(masked_row_text: str) -> bool:
    """Report whether a patient row is a short answer that opens with a denial.

    Args:
        masked_row_text: Patient row with epistemic phrases masked, so "no idea"
            is never a denial answer.

    Returns:
        True for answers like "No." or "No, I haven't noticed anything like
        that." - at most eight lexical words, starting with a denial word, and
        free of affirmative content.
    """
    words = re.findall(r"[a-z']+", masked_row_text)
    # Empty or non-denial openings cannot be the user's short negative answer.
    if not words or words[0] not in _ANSWER_DENIAL_OPENERS:
        return False
    # Long rows may contain unrelated context, so they cannot inherit a question.
    if len(words) > _SHORT_ANSWER_MAX_WORDS:
        return False

    return _AFFIRMATIVE_ANSWER_PATTERN.search(masked_row_text) is None


def _is_short_normal_screen_answer(masked_row_text: str) -> bool:
    """Recognize a brief normal answer to the clinician's screening list.

    Args:
        masked_row_text: Patient answer after uncertainty phrases are removed;
            empty means the user supplied no usable screening response.

    Returns:
        True only for at-most-eight-word answers such as "that's all fine";
        False keeps the generated denial visibly unverified.
    """
    words = re.findall(r"[a-z']+", masked_row_text)
    # Empty or long replies cannot inherit the clinician's whole question list.
    if not words or len(words) > _SHORT_ANSWER_MAX_WORDS:
        return False

    return (
        _SHORT_NORMAL_SCREEN_ANSWER_PATTERN.fullmatch(masked_row_text.strip())
        is not None
    )


def _folded_patient_denial_answer(
    clinician_row_text: str,
) -> tuple[str, str] | None:
    """Recover a short patient answer folded onto the next clinician UI row.

    Args:
        clinician_row_text: One DOCTOR-labelled corrected row; empty means no folded answer.

    Returns:
        The leading denial and clinician continuation when both are present;
        None keeps ordinary clinician wording out of patient evidence.
    """
    folded_answer = _FOLDED_PATIENT_DENIAL_PATTERN.fullmatch(clinician_row_text)
    # A clinician row without a punctuated leading "No" has no mixed answer to recover.
    if folded_answer is None:
        return None
    continuation = folded_answer.group("continuation").strip()
    # Explanatory clinician prose is not a patient answer, even if it starts after "No."
    if _FOLDED_CLINICIAN_CONTINUATION_PATTERN.match(continuation) is None:
        return None

    return folded_answer.group("answer").lower(), continuation


def _immediate_denial_question_context(
    answer_row_index: int, normalized_rows: list[dict[str, str]]
) -> str:
    """Join the clinician question immediately answered by one short response.

    Args:
        answer_row_index: Row containing the user's answer; zero has no question context.
        normalized_rows: One visit's ordered rows; empty means no answer pairing exists.

    Returns:
        Clinician fragments since the prior patient/folded answer; empty means the short
        response cannot verify a generated denial.
    """
    clinician_fragments: list[str] = []
    # Walk backward only through the question attached to this answer.
    for earlier_row in reversed(normalized_rows[:answer_row_index]):
        # A prior patient turn closes the question/answer exchange shown in the UI.
        if earlier_row["role"] == "PATIENT":
            break
        # Unknown/system rows also prevent an answer from borrowing older questions.
        if earlier_row["role"] != "DOCTOR":
            break
        folded_answer = _folded_patient_denial_answer(earlier_row["text"])
        # A prior folded answer closes its question; its continuation starts this one.
        if folded_answer is not None:
            clinician_fragments.append(folded_answer[1])
            break
        clinician_fragments.append(earlier_row["text"])

    return " ".join(reversed(clinician_fragments))


def _same_patient_row_question_denial_context(
    answer_row_index: int,
    patient_row_text: str,
    normalized_rows: list[dict[str, str]],
) -> str | None:
    """Recover a question tail and short answer merged into one patient UI row.

    Args:
        answer_row_index: PATIENT row containing both the question tail and answer.
        patient_row_text: Lowercased row text; empty means no recoverable exchange.
        normalized_rows: One visit's ordered rows; empty supplies no earlier question.

    Returns:
        Complete local question context when the row ends in `? No.`;
        None keeps ordinary patient questions out of denial evidence.
    """
    same_row_exchange = _PATIENT_ROW_QUESTION_TAIL_DENIAL_PATTERN.fullmatch(
        patient_row_text
    )
    # A normal patient row has no clinician question tail to recover.
    if same_row_exchange is None:
        return None
    preceding_question_context = _immediate_denial_question_context(
        answer_row_index, normalized_rows
    )

    return f"{preceding_question_context} {same_row_exchange.group('question')}".strip()


def _recent_denial_question_context(
    answer_row_index: int, normalized_rows: list[dict[str, str]]
) -> str:
    """Join the bounded clinician fragments that one short answer responds to.

    Args:
        answer_row_index: Row containing the user's short answer; zero has no question context.
        normalized_rows: One visit's ordered rows; empty means no question can be inherited.

    Returns:
        Up to six preceding clinician fragments in spoken order; empty means the answer
        cannot verify any topic in the generated note.
    """
    clinician_fragments: list[str] = []
    # Walk backward so ASR cards between question fragments do not consume locality.
    for earlier_row in reversed(normalized_rows[:answer_row_index]):
        # Only clinician words define the question inherited by a short patient answer.
        if earlier_row["role"] != "DOCTOR":
            continue
        clinician_fragments.append(earlier_row["text"])
        # Six clinician fragments is the existing question-locality boundary.
        if len(clinician_fragments) == _RECENT_DENIAL_QUESTION_ROWS:
            break

    return " ".join(reversed(clinician_fragments))


def _question_context_supports_denied_topic(
    answer_row_index: int,
    normalized_rows: list[dict[str, str]],
    topic: str,
    required_words: int,
) -> bool:
    """Check whether the nearby clinician question names one denied topic.

    Args:
        answer_row_index: Row containing the user's bounded negative/normal answer.
        normalized_rows: One visit's ordered rows; empty supports nothing.
        topic: Cleaned topic displayed in the note; empty supports nothing.
        required_words: Minimum distinct topic words required for safe matching.

    Returns:
        True when the immediate question supports the topic, or overlaps it before a
        bounded earlier fragment completes the compound wording.
    """
    immediate_question_context = _immediate_denial_question_context(
        answer_row_index, normalized_rows
    )
    immediate_topic_word_count = _matched_topic_words(immediate_question_context, topic)
    # A complete match in the answer's own question needs no earlier context.
    if (
        topic in immediate_question_context
        or immediate_topic_word_count >= required_words
    ):
        return True
    # Zero overlap means this answer belongs to a different clinician question.
    if immediate_topic_word_count == 0:
        return False
    recent_question_context = _recent_denial_question_context(
        answer_row_index, normalized_rows
    )

    return (
        topic in recent_question_context
        or _matched_topic_words(recent_question_context, topic) >= required_words
    )


def _did_patient_deny_topic(topic: str, normalized_rows: list[dict[str, str]]) -> bool:
    """Report whether the patient actually denied one topic.

    Args:
        topic: Cleaned denied-topic phrase from the note.
        normalized_rows: Lowercased transcript rows in spoken order.

    Returns:
        True when patient wording covers the topic in one clause or a bounded
        short answer whose immediate clinician question overlaps that topic.
    """
    required_words = _topic_support_requirement(topic)
    # Each visit row is checked for direct, short, or tightly recovered denial evidence.
    for row_index, row in enumerate(normalized_rows):
        # A corrected row may begin with a patient "No." folded onto clinician follow-up.
        if row["role"] == "DOCTOR":
            folded_answer = _folded_patient_denial_answer(row["text"])
            # Ordinary clinician prose never becomes patient evidence.
            if folded_answer is None:
                continue
            # The recovered answer verifies only its bounded preceding question.
            if _question_context_supports_denied_topic(
                row_index, normalized_rows, topic, required_words
            ):
                return True
            continue

        # Unknown/system rows cannot establish what the patient denied.
        if row["role"] != "PATIENT":
            continue
        same_row_question_context = _same_patient_row_question_denial_context(
            row_index, row["text"], normalized_rows
        )
        # A merged `question? No.` verifies only the topic in that complete local question.
        if same_row_question_context is not None and (
            topic in same_row_question_context
            or _matched_topic_words(same_row_question_context, topic) >= required_words
        ):
            return True
        masked = _mask_epistemic_phrases(row["text"])

        # Direct support: a real denial and the topic inside the SAME clause,
        # so a lip mention early in a monologue cannot pair with a "no" at its
        # end (the day3 laundering hole).
        for clause in _denial_evidence_clauses(masked):
            # A clause without the patient's negative wording proves nothing.
            if _PATIENT_DENIAL_PATTERN.search(clause) is None:
                continue
            # The denial and its clinical topic must share this local clause.
            if topic in clause or _matched_topic_words(clause, topic) >= required_words:
                return True

        # Short-answer support: "No." counts when a CLINICIAN row named the
        # topic shortly before - six rows back, because live transcripts split
        # one question across several fragment rows.
        inherits_question = _is_short_denial_answer(
            masked
        ) or _is_short_normal_screen_answer(masked)
        # A short negative/normal answer can inherit its fragmented clinician question.
        if inherits_question:
            # Joined fragments verify compound topics such as "shortness of breath".
            if _question_context_supports_denied_topic(
                row_index, normalized_rows, topic, required_words
            ):
                return True

    return False


def _exam_language_violation(
    sentence: str, normalized_rows: list[dict[str, str]]
) -> tuple[str, str] | None:
    """Check that examination-findings language matches a performed examination.

    Args:
        sentence: Note sentence possibly claiming exam activity or findings.
        normalized_rows: Lowercased transcript rows searched for performed-exam evidence.

    Returns:
        (reason, subtype), or None when the sentence is honest ("no exam documented")
        or an examination really happened.
    """
    # An exam that is only proposed/planned claims no findings either.
    if _EXAM_INTENT_PATTERN.search(sentence) is not None:
        return None

    exam_performed = any(
        # Only a clinician's performed-exam wording can support exam findings.
        row["role"] == "DOCTOR"
        and _EXAM_PERFORMANCE_PATTERN.search(row["text"]) is not None
        for row in normalized_rows
    )

    # "documented on examination" presupposes an examination happened even
    # inside a negative-finding sentence, so the honest-absence exemption
    # cannot launder it (the day1-c07 fever key point).
    if _EXAM_PRESUPPOSITION_PATTERN.search(sentence) is not None and not exam_performed:
        return (
            "this sentence presupposes an examination ('on examination'), but the"
            " transcript shows no examination was performed",
            "exam-presupposed-without-performance",
        )

    # Honest absence ("no examination findings documented") is the wanted behavior.
    if _EXAM_ABSENCE_PATTERN.search(sentence) is not None:
        return None

    claims_exam = any(
        pattern.search(sentence) is not None for pattern in _EXAM_CLAIM_PATTERNS
    )
    # Sentences without exam language have nothing to prove.
    if not claims_exam:
        return None

    # A transcript row proving a performed exam legitimises the claim.
    if exam_performed:
        return None

    return (
        "this sentence uses examination-findings language, but the transcript shows no"
        " examination was performed - screening answers are history, not exam findings",
        "exam-claim-without-performance",
    )


# --- Detector family 3: temporal-state / action-state review reasons ------------
# Internal reason lane only (no payload change; the summary lanes own wording/surfacing).
# Bounded per the detector family specs: note-final lanes (Plan, Assessment,
# key points) except the medication-scope check, which is ungated because the
# reproduced a03 specimen lives in Subjective (recorded spec amendment).

_NOTE_FINAL_HEADINGS = frozenset({"plan", "assessment"})

STATE_SUPERSEDED_REASON = "state_superseded"
ACTION_NOT_CONFIRMED_DONE_REASON = "action_not_confirmed_done"
TEMPORAL_CONTEXT_LOST_REASON = "temporal_context_lost"

# "no antihistamines immediately available" / "does not have" - the note-final
# absence assertions whose object a later source row can reverse.
_STATE_ABSENCE_CLAIM_PATTERN = re.compile(
    r"\b(?:no|not|without)\b(?P<object>[^.;,]{0,60}?)\bavailab(?:le|ility)\b"
    r"|\bdoes not have\b(?P<object_have>[^.;]{0,60})",
    re.IGNORECASE,
)
_STATE_NEGATION_PATTERN = re.compile(
    r"\b(?:no|not|don'?t|none|without|haven'?t)\b", re.IGNORECASE
)
_STATE_AFFIRMATION_PATTERN = re.compile(
    r"\b(?:yes|yeah|yep|we do|we have|found|got some)\b", re.IGNORECASE
)
# A claim narrating the reversal itself ("initially none; later found") is
# honest and stays unflagged.
_STATE_NARRATIVE_MARKER_PATTERN = re.compile(
    r"\b(?:initially|later|subsequently|eventually)\b", re.IGNORECASE
)

# Completion asserted in note-final prose; "clinician arranges" (in progress)
# and "to call" (instruction) never match.
_ACTION_COMPLETION_VERB_PATTERN = re.compile(
    r"\b(?:called|ordered|arranged|scheduled|booked|completed|dispatched|administered)\b",
    re.IGNORECASE,
)
_ACTION_ADMINISTRATION_PATTERN = re.compile(
    r"\b(?:included|given)\b[^.;]{0,60}\badministration\b"
    r"|\badministration\b[^.;]{0,40}\b(?:given|completed)\b",
    re.IGNORECASE,
)
# Words directly before a completion verb that mark future/instruction framing.
_ACTION_FUTURE_FRAME_WORDS = frozenset(
    {"to", "will", "would", "instructed", "advised", "recommended", "be"}
)
# Source forms that promise, instruct, recommend, question, or condition an
# action rather than confirming it happened.
_ACTION_PROMISE_FORM_PATTERN = re.compile(
    r"\bi'?ll\b|\bif you can\b|\bif you call\b|\bcould you\b|\bcan you\b"
    r"|\byou can\b|\bwe need to\b|\bneed to\b|\bworth having\b|\bmight\b"
    r"|\bmay\b|\bwhether\b|\bjust call\b|\bcall the\b|\barranging\b|\?",
    re.IGNORECASE,
)
_ACTION_COMPLETION_EVIDENCE_PATTERN = re.compile(
    r"\b(?:has|have|had|was|were)\s+(?:been\s+)?"
    r"(?:called|ordered|arranged|scheduled|booked|completed|dispatched|administered)\b"
    r"|\bon (?:its|their) way\b"
    r"|\balready (?:called|ordered|arranged|scheduled|booked|completed|done)\b",
    re.IGNORECASE,
)
# Actor-future arrangement claims ("Patient to call back ... to arrange X"):
# the arrangement content itself must be spoken, or the plan was elaborated.
_ARRANGEMENT_CONTENT_CLAIM_PATTERN = re.compile(
    r"\bto arrange\b(?P<content>[^.;]{0,90})", re.IGNORECASE
)
# Parenthetical hedges ("(presumed epinephrine auto-injectors)") flag their own
# uncertainty; hedged content is not held to the support requirement.
_PARENTHETICAL_PATTERN = re.compile(r"\([^)]*\)")

# Separate explicit compound Plan actions so one source action cannot confirm another.
_ACTION_PROPOSITION_SEPARATOR_PATTERN = re.compile(r";|,\s+(?=with\b)", re.IGNORECASE)

_TIME_RANGE_CLAIM_PATTERN = re.compile(
    r"\b(\d{1,3})\s*(?:-|–|-|to)\s*(\d{1,3})\s*(hours?|days?|weeks?)\b",
    re.IGNORECASE,
)
_SOURCE_TIME_RANGE_PATTERN = re.compile(
    r"\b([a-z\d-]+(?: [a-z\d-]+)?)\s+to\s+([a-z\d-]+(?: [a-z\d-]+)?)"
    r"\s+(hours?|days?|weeks?)\b",
    re.IGNORECASE,
)
_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}

_NO_MEDICATIONS_CLAIM_PATTERN = re.compile(
    r"\bno (?:current )?medications?\b(?!\s+allerg)", re.IGNORECASE
)
_FIRST_PERSON_INTAKE_PATTERN = re.compile(
    r"\bi(?:'ve| have)?\s+(?:been\s+)?tak(?:ing|en)\b|\bi took\b|\bi'?m taking\b",
    re.IGNORECASE,
)
# "there isn't anything that I'm taking" is the denial being summarized, not
# evidence of intake; windows carrying it stay out of the contradiction.
_INTAKE_DENIAL_CONTEXT_PATTERN = re.compile(
    r"\bisn'?t anything\b|\bnothing\b|\bnot taking\b", re.IGNORECASE
)

# Words that never identify an action object or arrangement content.
_ACTION_OBJECT_STOPWORDS = frozenset(
    "the and with for was were been has have had also all any are but can"
    " into from that this those these when while where which will would could"
    " patient mother clinician doctor person immediate immediately urgent"
    " emergency directed instructed advised call called ordered arranged"
    " dispatched administered administration request suspected possible"
    " pending awaiting management interim included assessed presumed check"
    " exclude other causes location".split()
)


def temporal_action_review_reasons(
    sections: list[dict[str, Any]],
    key_points: list[str],
    transcript_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Machine-readable temporal/action-state reasons for one generated note.

    Use downstream (review surfaces): the browser payload is
    unchanged and nothing here retries generation. Empty inputs produce no
    reasons.

    Args:
        sections: Generated SOAP sections; empty means nothing to review.
        key_points: TL;DR lines; empty means the strip is hidden.
        transcript_rows: The note's selected source rows; empty disables
            every source comparison, so nothing can be honestly flagged.

    Returns:
        One entry per failing sentence: {section, sentence, reason, detail,
        segment_ids}; empty means every reviewed claim held.
    """
    # Without source rows there is no evidence to contradict a claim.
    if not transcript_rows:
        return []

    source_rows = [
        {
            "segment_id": str(row.get("segment_id", "")),
            "role": str(row.get("role", "")).upper(),
            "text": str(row.get("text", "") or "").lower(),
        }
        for row in transcript_rows
    ]

    review_reasons: list[dict[str, Any]] = []
    # Sections first, key points last - the same order the clinician reads.
    for location, sentence, is_note_final in _located_note_sentences(
        sections, key_points
    ):
        # The medication-scope check is ungated: a status claim misleads from
        # any section (the reproduced specimen sits in Subjective).
        review_reasons.extend(
            _medication_scope_reasons(location, sentence, source_rows)
        )
        # The remaining patterns judge note-final state/action claims only;
        # Subjective narration is history, not a final state assertion.
        if not is_note_final:
            continue
        review_reasons.extend(
            _state_superseded_reasons(location, sentence, source_rows)
        )
        review_reasons.extend(
            _action_completion_reasons(location, sentence, source_rows)
        )
        review_reasons.extend(_time_range_reasons(location, sentence, source_rows))

    return review_reasons


def _located_note_sentences(
    sections: list[dict[str, Any]],
    key_points: list[str],
) -> list[tuple[str, str, bool]]:
    """Yield every visible note sentence with its location and lane gate."""
    located: list[tuple[str, str, bool]] = []
    # Every section sentence is judged where the clinician reads it.
    for section in sections:
        heading = str(section.get("heading", ""))
        is_note_final = heading.strip().lower() in _NOTE_FINAL_HEADINGS
        for sentence in _sentences(str(section.get("content", ""))):
            located.append((f"section '{heading}'", sentence, is_note_final))
    # Key points always speak in the note's final voice.
    for key_point in key_points:
        for sentence in _sentences(str(key_point)):
            located.append(("key_points", sentence, True))
    return located


def _meaningful_object_tokens(text: str) -> set[str]:
    """Nouns-ish tokens that can identify an action object in source rows."""
    return {
        _singular_token(token)
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) >= 3 and token not in _ACTION_OBJECT_STOPWORDS
    }


def _singular_token(token: str) -> str:
    """Fold trailing plurals so 'tests' finds the spoken 'test'."""
    return token[:-1] if len(token) > 3 and token.endswith("s") else token


def _rows_with_any_token(
    source_rows: list[dict[str, str]], object_tokens: set[str]
) -> list[int]:
    """Indexes of source rows containing at least one object token."""
    matching_indexes: list[int] = []
    for row_index, row in enumerate(source_rows):
        row_tokens = {
            _singular_token(token) for token in re.findall(r"[a-z0-9]+", row["text"])
        }
        # Any shared object token makes this row part of the action's evidence.
        if row_tokens & object_tokens:
            matching_indexes.append(row_index)
    return matching_indexes


def _window_text(source_rows: list[dict[str, str]], row_index: int, radius: int) -> str:
    """Join a row with its neighbors; split questions/answers stay together."""
    window_start = max(0, row_index - radius)
    return " ".join(
        row["text"] for row in source_rows[window_start : row_index + radius + 1]
    )


def _state_superseded_reasons(
    location: str,
    sentence: str,
    source_rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Flag a note-final absence claim whose object a later row affirms."""
    absence_match = _STATE_ABSENCE_CLAIM_PATTERN.search(sentence)
    # Sentences that assert no absence have no state to supersede.
    if absence_match is None:
        return []
    # A claim narrating the reversal itself is the honest form.
    if _STATE_NARRATIVE_MARKER_PATTERN.search(sentence):
        return []

    object_text = (
        absence_match.group("object") or absence_match.group("object_have") or ""
    )
    object_tokens = _meaningful_object_tokens(object_text)
    # An absence without a nameable object cannot be traced to rows.
    if not object_tokens:
        return []

    object_row_indexes = _rows_with_any_token(source_rows, object_tokens)
    absence_indexes = [
        row_index
        for row_index in object_row_indexes
        if _STATE_NEGATION_PATTERN.search(_window_text(source_rows, row_index, 2))
    ]
    affirmation_indexes = [
        row_index
        for row_index in object_row_indexes
        if _STATE_AFFIRMATION_PATTERN.search(_window_text(source_rows, row_index, 2))
    ]
    # The state is superseded only when an affirmation follows an absence.
    if not absence_indexes or not affirmation_indexes:
        return []
    if max(affirmation_indexes) <= min(absence_indexes):
        return []

    return [
        {
            "section": location,
            "sentence": sentence,
            "reason": STATE_SUPERSEDED_REASON,
            "detail": "a later source row reverses the asserted absence",
            "segment_ids": [
                source_rows[row_index]["segment_id"]
                for row_index in (min(absence_indexes), max(affirmation_indexes))
            ],
        }
    ]


def _action_completion_reasons(
    location: str,
    sentence: str,
    source_rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Flag completed Plan wording that lacks matching source proof.
    Use before the clinician reviews the generated note.
    """
    review_reasons: list[dict[str, Any]] = []
    # Split combined next steps so each visible action keeps its own evidence.
    action_propositions = [
        action_proposition.strip()
        for action_proposition in _ACTION_PROPOSITION_SEPARATOR_PATTERN.split(
            _PARENTHETICAL_PATTERN.sub(" ", sentence)
        )
        if action_proposition.strip()
    ]
    # One unsupported action is enough to mark the clinician-visible sentence.
    for action_proposition in action_propositions:
        asserted_completion_verbs: list[str] = []
        # Completed-state wording tells the clinician this action already happened.
        for completion_verb_match in _ACTION_COMPLETION_VERB_PATTERN.finditer(
            action_proposition
        ):
            preceding_words = re.findall(
                r"[a-z']+",
                action_proposition[: completion_verb_match.start()].lower(),
            )
            # Future or instructed wording remains a next step, not a completed action.
            if preceding_words and preceding_words[-1] in _ACTION_FUTURE_FRAME_WORDS:
                continue
            asserted_completion_verbs.append(completion_verb_match.group(0))
        administration_match = _ACTION_ADMINISTRATION_PATTERN.search(action_proposition)
        # An administration statement also tells the clinician treatment was given.
        if administration_match is not None:
            asserted_completion_verbs.append(administration_match.group(0))
        # A proposition without a completed state needs no completion warning.
        if not asserted_completion_verbs:
            continue

        action_object_tokens = _meaningful_object_tokens(action_proposition)
        matching_source_row_indexes = _rows_with_any_token(
            source_rows, action_object_tokens
        )
        # Prospective wording means this action still needs completion proof.
        promise_source_row_indexes = [
            row_index
            for row_index in matching_source_row_indexes
            if _ACTION_PROMISE_FORM_PATTERN.search(
                _window_text(source_rows, row_index, 1)
            )
        ]
        # Only explicit completed-state wording confirms this same action.
        has_completion_evidence = any(
            _ACTION_COMPLETION_EVIDENCE_PATTERN.search(source_rows[row_index]["text"])
            for row_index in matching_source_row_indexes
        )
        # A promised action without matching completion evidence needs clinician review.
        if promise_source_row_indexes and not has_completion_evidence:
            review_reasons.append(
                {
                    "section": location,
                    "sentence": sentence,
                    "reason": ACTION_NOT_CONFIRMED_DONE_REASON,
                    "detail": "source rows promise or instruct this action;"
                    " none confirms it was completed",
                    "segment_ids": [
                        source_rows[row_index]["segment_id"]
                        for row_index in promise_source_row_indexes[:4]
                    ],
                }
            )
            break

    review_reasons.extend(_arrangement_content_reasons(location, sentence, source_rows))
    return review_reasons


def _arrangement_content_reasons(
    location: str,
    sentence: str,
    source_rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Flag actor-future arrangements whose content was never spoken."""
    arrangement_match = _ARRANGEMENT_CONTENT_CLAIM_PATTERN.search(sentence)
    # Sentences without a "to arrange X" plan make no arrangement claim.
    if arrangement_match is None:
        return []

    unhedged_content = _PARENTHETICAL_PATTERN.sub(
        " ", arrangement_match.group("content")
    )
    content_tokens = _meaningful_object_tokens(unhedged_content)
    if not content_tokens:
        return []

    spoken_tokens: set[str] = set()
    # The arrangement may be spoken anywhere in the visit, not only nearby.
    for row in source_rows:
        spoken_tokens.update(
            _singular_token(token) for token in re.findall(r"[a-z0-9]+", row["text"])
        )
    unsupported_tokens = sorted(content_tokens - spoken_tokens)
    # Fully spoken arrangement content is a faithful plan restatement.
    if not unsupported_tokens:
        return []

    return [
        {
            "section": location,
            "sentence": sentence,
            "reason": ACTION_NOT_CONFIRMED_DONE_REASON,
            "detail": "arrangement content never spoken: "
            + ", ".join(unsupported_tokens),
            "segment_ids": [],
        }
    ]


def _word_quantity(quantity_text: str) -> int | None:
    """Parse '24', 'forty', or 'twenty-four'; None when not a quantity.

    The range capture can drag a leading filler word along ("about
    twenty-four"), so the longest all-number suffix is the spoken quantity.
    """
    parts = [part for part in re.split(r"[- ]", quantity_text.strip().lower()) if part]
    number_start = len(parts)
    # Walk backward while every part still reads as a number.
    for part_index in range(len(parts) - 1, -1, -1):
        if parts[part_index] not in _NUMBER_WORDS and not parts[part_index].isdigit():
            break
        number_start = part_index

    number_parts = parts[number_start:]
    if not number_parts:
        return None

    total = sum(
        int(part) if part.isdigit() else _NUMBER_WORDS[part] for part in number_parts
    )
    return total if total > 0 else None


def _time_range_reasons(
    location: str,
    sentence: str,
    source_rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Flag a claimed time range that alters the spoken range."""
    review_reasons: list[dict[str, Any]] = []
    for claim_match in _TIME_RANGE_CLAIM_PATTERN.finditer(sentence):
        claim_range = (
            int(claim_match.group(1)),
            int(claim_match.group(2)),
            claim_match.group(3).lower().rstrip("s"),
        )
        source_ranges: list[tuple[int, int, str, str]] = []
        # A spoken range often splits across rows ("twenty-four to" /
        # "forty hours"), so each row is scanned joined with its neighbors.
        for row_index, row in enumerate(source_rows):
            joined_text = _window_text(source_rows, row_index, 1)
            for source_match in _SOURCE_TIME_RANGE_PATTERN.finditer(joined_text):
                low_value = _word_quantity(source_match.group(1))
                high_value = _word_quantity(source_match.group(2))
                # Non-quantity words ("go to sleep hours") are not a range.
                if low_value is None or high_value is None:
                    continue
                source_ranges.append(
                    (
                        low_value,
                        high_value,
                        source_match.group(3).lower().rstrip("s"),
                        row["segment_id"],
                    )
                )
        same_unit_ranges = [
            source_range
            for source_range in source_ranges
            if source_range[2] == claim_range[2]
        ]
        # With no spoken range there is nothing bounded to compare against.
        if not same_unit_ranges:
            continue
        # An exactly matching spoken range supports the claim.
        if any(
            source_range[0] == claim_range[0] and source_range[1] == claim_range[1]
            for source_range in same_unit_ranges
        ):
            continue
        review_reasons.append(
            {
                "section": location,
                "sentence": sentence,
                "reason": TEMPORAL_CONTEXT_LOST_REASON,
                "detail": "claimed range %d-%d %s differs from the spoken range"
                % (claim_range[0], claim_range[1], claim_range[2]),
                "segment_ids": sorted(
                    {source_range[3] for source_range in same_unit_ranges}
                ),
            }
        )
    return review_reasons


def _medication_scope_reasons(
    location: str,
    sentence: str,
    source_rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Flag 'no current medications' when the patient described taking some."""
    # Sentences without the status phrase make no medication-scope claim.
    if _NO_MEDICATIONS_CLAIM_PATTERN.search(sentence) is None:
        return []

    intake_row_ids = []
    # Intake statements split across rows ("I I've" / "been taking some"), so
    # each patient row is judged joined with its neighbors.
    for row_index, row in enumerate(source_rows):
        # Only the patient's own intake statements contradict the status.
        if row["role"] not in ("", "PATIENT"):
            continue
        joined_text = _window_text(source_rows, row_index, 1)
        # A denial of intake ("there isn't anything that I'm taking") is the
        # answer the claim summarized, not intake evidence against it.
        if _INTAKE_DENIAL_CONTEXT_PATTERN.search(joined_text):
            continue
        if _FIRST_PERSON_INTAKE_PATTERN.search(joined_text):
            intake_row_ids.append(row["segment_id"])
    if not intake_row_ids:
        return []

    return [
        {
            "section": location,
            "sentence": sentence,
            "reason": TEMPORAL_CONTEXT_LOST_REASON,
            "detail": "the patient described taking medication; the denial"
            " answered only the regular-treatment question",
            "segment_ids": intake_row_ids[:4],
        }
    ]


# --- Detector family 2: paired mental-health/risk answer review reasons ----------
# Internal reason lane only. Bounded per the detector family specs: a screen
# is a DOCTOR question matching the form list; the paired answer is the
# patient's full uninterrupted turn (spec amendment: the six-row bound was too
# tight for fragmented ASR - c01's answer spans eleven consecutive rows and
# the qualifier sits at its tail; the natural turn boundary is the bound).

RISK_ANSWER_CONTEXT_LOST_REASON = "risk_answer_context_lost"

# Bounded risk/mental-health screen forms; anything else (e.g. the a20
# concentration-or-tiredness branch question) is deliberately out of scope.
_RISK_SCREEN_QUESTION_PATTERN = re.compile(
    r"\bmood\b[^.;?]{0,60}\b(?:so\s+)?low\b"
    r"|\bcouldn'?t carry on\b"
    r"|\bsuicid"
    r"|\bself[- ]harm\b"
    r"|\bharm(?:ing)? (?:yourself|others)\b"
    r"|\bpanic attacks?\b"
    r"|\bso overwhelmed\b",
    re.IGNORECASE,
)
# Topic phrases a claim must contain before it counts as representing the
# screen's answer; descriptive uses ("morning panic about being late") do not.
_RISK_TOPIC_CLAIM_PATTERNS = {
    "suicidal": re.compile(r"\bsuicid(?:al|e)?\b", re.IGNORECASE),
    "self-harm": re.compile(r"\bself[- ]harm\b", re.IGNORECASE),
    "panic attack": re.compile(r"\bpanic attacks?\b", re.IGNORECASE),
}
_RISK_TOPIC_ANSWER_PATTERNS = {
    "suicidal": re.compile(r"\bsuicid", re.IGNORECASE),
    "self-harm": re.compile(r"\bself[- ]harm\b", re.IGNORECASE),
    "panic attack": re.compile(r"\bpanic attacks?\b", re.IGNORECASE),
}
_RISK_DENIAL_PATTERN = re.compile(
    r"\bno\b|\bnot\b|\bhaven'?t\b|\bnever\b|\bdon'?t\b", re.IGNORECASE
)
_RISK_CLAIM_NEGATION_PATTERN = re.compile(
    r"\bno\b|\bnot\b|\bdenie[sd]\b|\bnegative\b|\bwithout\b", re.IGNORECASE
)
# Hedges that soften a risk answer; "wouldn't say so" is the standalone
# anaphoric form - "wouldn't say that I've had any suicidal thoughts" is a
# full denial complement and deliberately not a hedge.
_RISK_HEDGE_PATTERN = re.compile(
    r"\bi think\b|\bmaybe\b|\bnot sure\b|\bi guess\b|\bwouldn'?t say so\b"
    r"|\bnot that i recall\b",
    re.IGNORECASE,
)
# A qualifier is a contrast marker followed by first-person content in the
# same answer turn ("It's just that I don't want to go on like this").
_RISK_QUALIFIER_PATTERN = re.compile(
    r"(?:\bbut\b|\bit'?s just\b|\bjust that\b)\s+(?P<clause>[^.?]{5,90})",
    re.IGNORECASE,
)
_RISK_FIRST_PERSON_PATTERN = re.compile(r"\bi\b|\bi'm\b|\bi'?ve\b", re.IGNORECASE)
# How many consecutive qualifier tokens must survive into the note before the
# qualifier counts as preserved.
_RISK_QUALIFIER_MATCH_TOKENS = 4
# Safety cap on the patient's uninterrupted answer turn.
_RISK_ANSWER_TURN_MAX_ROWS = 12


def risk_pair_review_reasons(
    sections: list[dict[str, Any]],
    key_points: list[str],
    transcript_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Machine-readable reasons for risk-screen answers a note misrepresents.

    Use downstream (review surfaces): the browser payload is
    unchanged and nothing here retries generation. Notes and visits without a
    detected risk screen produce no reasons.

    Args:
        sections: Generated SOAP sections; empty means nothing to review.
        key_points: TL;DR lines; empty means the strip is hidden.
        transcript_rows: The note's selected source rows; empty means no
            screen can be detected, so nothing can be honestly flagged.

    Returns:
        One entry per failing claim: {section, sentence, reason, detail,
        segment_ids}; empty means every screen answer is represented intact.
    """
    if not transcript_rows:
        return []

    source_rows = [
        {
            "segment_id": str(row.get("segment_id", "")),
            "role": str(row.get("role", "")).upper(),
            "text": str(row.get("text", "") or "").lower(),
        }
        for row in transcript_rows
    ]
    screen_pairs = _risk_screen_pairs(source_rows)
    # No detected screen means this family has nothing to judge.
    if not screen_pairs:
        return []

    note_tokens = _whole_note_tokens(sections, key_points)
    review_reasons: list[dict[str, Any]] = []
    for location, sentence, _is_note_final in _located_note_sentences(
        sections, key_points
    ):
        for screen_pair in screen_pairs:
            review_reasons.extend(
                _risk_claim_reasons(location, sentence, screen_pair, note_tokens)
            )
    return review_reasons


def _whole_note_tokens(
    sections: list[dict[str, Any]], key_points: list[str]
) -> tuple[str, ...]:
    """All note words in reading order, for qualifier-preservation checks."""
    note_texts = [str(section.get("content", "")) for section in sections]
    note_texts.extend(str(key_point) for key_point in key_points)
    return _normalized_lexical_tokens(" ".join(note_texts))


def _risk_screen_pairs(source_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Detect answered risk screens: question rows plus the patient's turn.

    Question forms split across rows are found on +-1 joined windows; the
    answer is the following uninterrupted run of PATIENT rows.
    """
    screen_pairs: list[dict[str, Any]] = []
    claimed_answer_starts: set[int] = set()
    for row_index, row in enumerate(source_rows):
        # Only the clinician's wording can open a screen.
        if row["role"] != "DOCTOR":
            continue
        if not _RISK_SCREEN_QUESTION_PATTERN.search(
            _window_text(source_rows, row_index, 1)
        ):
            continue

        answer_start = _next_patient_row_index(source_rows, row_index)
        # An unanswered screen is the coverage lane's concern, not this one.
        if answer_start is None or answer_start in claimed_answer_starts:
            continue
        claimed_answer_starts.add(answer_start)

        answer_rows = []
        for answer_row in source_rows[
            answer_start : answer_start + _RISK_ANSWER_TURN_MAX_ROWS
        ]:
            # The turn ends when another speaker takes over.
            if answer_row["role"] != "PATIENT":
                break
            answer_rows.append(answer_row)

        answer_text = " ".join(answer_row["text"] for answer_row in answer_rows)
        pair_text = f"{_window_text(source_rows, row_index, 1)} {answer_text}"
        topics = {
            topic
            for topic, pattern in _RISK_TOPIC_ANSWER_PATTERNS.items()
            if pattern.search(pair_text)
        }
        qualifier_match = None
        # The LAST contrast clause in the turn carries the closing qualifier.
        for candidate in _RISK_QUALIFIER_PATTERN.finditer(answer_text):
            if _RISK_FIRST_PERSON_PATTERN.search(candidate.group("clause")):
                qualifier_match = candidate

        screen_pairs.append(
            {
                "segment_ids": [row["segment_id"]]
                + [answer_row["segment_id"] for answer_row in answer_rows],
                "topics": topics,
                "denied": bool(_RISK_DENIAL_PATTERN.search(answer_text)),
                "hedged": bool(_RISK_HEDGE_PATTERN.search(answer_text)),
                "qualifier_tokens": _normalized_lexical_tokens(
                    qualifier_match.group("clause")
                )
                if qualifier_match
                else (),
            }
        )
    return screen_pairs


def _next_patient_row_index(
    source_rows: list[dict[str, str]], question_index: int
) -> int | None:
    """First PATIENT row after the question; None when the visit ends first."""
    for row_index in range(question_index + 1, len(source_rows)):
        if source_rows[row_index]["role"] == "PATIENT":
            return row_index
        # A substantive clinician continuation means the question moved on.
        if (
            source_rows[row_index]["role"] == "DOCTOR"
            and row_index > question_index + 3
        ):
            return None
    return None


def _risk_claim_reasons(
    location: str,
    sentence: str,
    screen_pair: dict[str, Any],
    note_tokens: tuple[str, ...],
) -> list[dict[str, Any]]:
    """Judge one note claim against one answered risk screen."""
    claimed_topics = {
        topic
        for topic in screen_pair["topics"]
        if _RISK_TOPIC_CLAIM_PATTERNS[topic].search(sentence)
    }
    # Claims that never name the screened topic do not represent its answer.
    if not claimed_topics:
        return []

    dropped_details: list[str] = []
    # A denied topic asserted positively inverts the patient's answer.
    if screen_pair["denied"] and not _RISK_CLAIM_NEGATION_PATTERN.search(sentence):
        dropped_details.append("denial_inverted")
    # A hedged answer cannot become an unhedged claim.
    if screen_pair["hedged"] and not _contains_any_phrase(
        sentence.lower(), _NOTE_UNCERTAINTY_MARKERS
    ):
        dropped_details.append("hedge_dropped")
    # The answer's closing qualifier must survive somewhere in the note.
    qualifier_tokens = screen_pair["qualifier_tokens"]
    if qualifier_tokens and not _tokens_contain_run(
        note_tokens, qualifier_tokens, _RISK_QUALIFIER_MATCH_TOKENS
    ):
        dropped_details.append("qualifier_dropped")

    if not dropped_details:
        return []

    return [
        {
            "section": location,
            "sentence": sentence,
            "reason": RISK_ANSWER_CONTEXT_LOST_REASON,
            "detail": ", ".join(dropped_details),
            "segment_ids": screen_pair["segment_ids"][:6],
        }
    ]


def _tokens_contain_run(
    note_tokens: tuple[str, ...],
    qualifier_tokens: tuple[str, ...],
    run_length: int,
) -> bool:
    """Whether any qualifier token run of the given length survives verbatim."""
    # Short qualifiers must survive whole; longer ones by any full-length run.
    window = min(run_length, len(qualifier_tokens))
    if window == 0:
        return True
    for start in range(len(qualifier_tokens) - window + 1):
        if _contains_token_sequence(
            note_tokens, qualifier_tokens[start : start + window]
        ):
            return True
    return False


# --- Detector family 5a: unsupported demographic review reasons -------------------
# Internal reason lane only. Exact age needs a SPOKEN age statement (spoken DOB
# alone cannot derive age - CONTRACTS section 3; 0.4.0 has no trusted encounter
# metadata); sex needs an explicit statement or a clinician address term.

UNSUPPORTED_DEMOGRAPHIC_REASON = "unsupported_demographic"

_AGE_CLAIM_PATTERN = re.compile(r"\b(\d{1,3})[- ]year[- ]old\b", re.IGNORECASE)
# The sex word must sit in the descriptor position right after the age.
_DESCRIPTOR_SEX_PATTERN = re.compile(
    r"year[- ]old\s+(?:\w+\s+){0,2}?(male|female|man|woman)\b", re.IGNORECASE
)
_MALE_EVIDENCE_PATTERN = re.compile(
    r"\bsir\b|\bmr\b|\bi'?m a man\b|\bi'?m male\b", re.IGNORECASE
)
_FEMALE_EVIDENCE_PATTERN = re.compile(
    r"\bmadam\b|\bma'am\b|\bmrs\b|\bms\b|\bmiss\b|\bi'?m a woman\b|\bi'?m female\b",
    re.IGNORECASE,
)
_TENS_WORDS = {
    20: "twenty",
    30: "thirty",
    40: "forty",
    50: "fifty",
    60: "sixty",
    70: "seventy",
    80: "eighty",
    90: "ninety",
}
_UNITS_WORDS = {
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
}


def unsupported_demographic_review_reasons(
    sections: list[dict[str, Any]],
    key_points: list[str],
    transcript_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Machine-readable reasons for demographics no source statement supports.

    Use downstream (review surfaces): the browser payload is
    unchanged. A DOB restatement without derivation is exempt; only the
    derived descriptor ("NN-year-old woman") is judged.

    Args:
        sections: Generated SOAP sections; empty means nothing to review.
        key_points: TL;DR lines; empty means the strip is hidden.
        transcript_rows: The note's selected source rows; empty means no
            support can exist, so any descriptor claim flags.

    Returns:
        One entry per failing claim: {section, sentence, reason, detail,
        segment_ids}; empty means every demographic descriptor is spoken.
    """
    source_rows = [
        {
            "segment_id": str(row.get("segment_id", "")),
            "role": str(row.get("role", "")).upper(),
            "text": str(row.get("text", "") or "").lower(),
        }
        for row in transcript_rows
    ]

    review_reasons: list[dict[str, Any]] = []
    for location, sentence, _is_note_final in _located_note_sentences(
        sections, key_points
    ):
        age_match = _AGE_CLAIM_PATTERN.search(sentence)
        # Sentences without the derived descriptor make no demographic claim.
        if age_match is None:
            continue

        unsupported_details: list[str] = []
        if not _age_is_spoken(int(age_match.group(1)), source_rows):
            unsupported_details.append(
                "exact age is not spoken (a DOB alone cannot derive it)"
            )

        sex_match = _DESCRIPTOR_SEX_PATTERN.search(sentence)
        if sex_match is not None and not _sex_is_supported(
            sex_match.group(1).lower(), source_rows
        ):
            unsupported_details.append(f"sex '{sex_match.group(1)}' is never stated")

        if not unsupported_details:
            continue

        review_reasons.append(
            {
                "section": location,
                "sentence": sentence,
                "reason": UNSUPPORTED_DEMOGRAPHIC_REASON,
                "detail": "; ".join(unsupported_details),
                "segment_ids": [],
            }
        )
    return review_reasons


def _spoken_age_forms(age: int) -> list[str]:
    """Digit and word forms a patient could have used for their age."""
    forms = [str(age)]
    if age in _TENS_WORDS:
        forms.append(_TENS_WORDS[age])
    tens, units = divmod(age, 10)
    if tens * 10 in _TENS_WORDS and units in _UNITS_WORDS:
        tens_word = _TENS_WORDS[tens * 10]
        units_word = _UNITS_WORDS[units]
        forms.extend([f"{tens_word}-{units_word}", f"{tens_word} {units_word}"])
    return forms


def _age_is_spoken(age: int, source_rows: list[dict[str, str]]) -> bool:
    """Whether any patient row states this exact age directly."""
    for row_index, row in enumerate(source_rows):
        # Only the patient's own statement supports their age.
        if row["role"] not in ("", "PATIENT"):
            continue
        window = _window_text(source_rows, row_index, 1)
        for age_form in _spoken_age_forms(age):
            if re.search(
                rf"\bi'?m {re.escape(age_form)}\b"
                rf"|\b{re.escape(age_form)} years? old\b",
                window,
            ):
                return True
    return False


def _sex_is_supported(claimed_sex: str, source_rows: list[dict[str, str]]) -> bool:
    """Whether an address term or explicit statement supports the sex claim."""
    evidence_pattern = (
        _MALE_EVIDENCE_PATTERN
        if claimed_sex in ("male", "man")
        else _FEMALE_EVIDENCE_PATTERN
    )
    return any(evidence_pattern.search(row["text"]) for row in source_rows)


# --- Hedge lane: hedged patient statements stay hedged in the note ------
# Visible lane (rule uncertainty-resolved, subtype hedge-dropped): a patient
# statement softened by a bounded hedge cannot verify a definite note claim -
# the claim needs an uncertainty marker or the verbatim patient quote. Hedges
# are matched only in PATIENT rows, so clinician reasoning ("I think you
# probably have...") never becomes patient uncertainty.

_PATIENT_HEDGE_PATTERN = re.compile(
    r"\bi think\b|\bmaybe\b|\bnot sure\b|\bi guess\b|\bwouldn'?t say so\b"
    r"|\bnot that i recall\b|\bi don'?t think so\b",
    re.IGNORECASE,
)
# Meaningful-token filter for hedge windows; grammar and hedge words per se
# never identify the hedged clinical content.
_HEDGE_CONTENT_STOPWORDS = frozenset(
    "the and but for was were been has have had that this with just bit"
    " sort um uh like really think guess maybe sure recall dont don so"
    " probably less more feeling feel feels felt generally in your you"
    " i'm im its it's".split()
)
# The hedge attaches locally: only tokens right beside it are the hedged
# content ("headache, maybe" / "Irregular I think"). Window-wide overlap was
# rejected in development - it flagged half of every hedge-rich consultation.
_HEDGE_ADJACENT_TOKEN_RADIUS = 2
# Equivocal denials hedge a NEGATION whose topic follows ("I don't think so
# ... no no weight change, just a bit"); the topic sits after the negation.
_EQUIVOCAL_DENIAL_HEDGE_PATTERN = re.compile(
    r"\bi don'?t think so\b|\bnot that i recall\b", re.IGNORECASE
)
_HEDGE_WINDOW_NEGATION_PATTERN = re.compile(r"\b(?:no|not)\b", re.IGNORECASE)
# Claim wording that already conveys approximation keeps the hedge honestly.
_HEDGE_CLAIM_SOFTENERS = (
    "approximately",
    "estimates",
    "estimated",
    "around",
    "about",
    "possible",
    "possibly",
    "unable to provide",
    "unable to say",
)


def _hedge_content_tokens(text: str) -> set[str]:
    """Tokens that can tie a note claim to a hedged patient statement."""
    return {
        _singular_token(token)
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) >= 3 and token not in _HEDGE_CONTENT_STOPWORDS
    }


def _hedged_statement_violation(
    sentence: str, normalized_rows: list[dict[str, str]]
) -> tuple[str, str] | None:
    """Check that a hedged patient statement is not resolved to definite.

    Args:
        sentence: Note sentence under review.
        normalized_rows: Lowercased transcript rows with roles.

    Returns:
        (reason, subtype), or None when the sentence preserves the hedge,
        quotes the patient verbatim, or restates nothing hedged.
    """
    lowered_sentence = sentence.lower()
    # A claim carrying its own uncertainty marker preserves the hedge.
    if _contains_any_phrase(lowered_sentence, _NOTE_UNCERTAINTY_MARKERS):
        return None
    # Approximation wording ("estimates 3-4 hours") also keeps the hedge.
    if _contains_any_phrase(lowered_sentence, _HEDGE_CLAIM_SOFTENERS):
        return None
    # A claim attributing uncertainty or memory failure to the patient is not
    # presenting definite content ("does not clearly recall ...").
    if _PATIENT_STATE_MARKER_PATTERN.search(sentence) is not None:
        return None
    claim_tokens = _hedge_content_tokens(sentence)
    # A claim with no substantive tokens cannot restate hedged content.
    if not claim_tokens:
        return None

    for row_index, row in enumerate(normalized_rows):
        # Only the patient's own hedges soften their statements.
        if row["role"] != "PATIENT":
            continue
        # Per the frozen family spec, hedges count only in ANSWER position -
        # a global scan flags conversational hedging all over the visit.
        if not _hedge_row_answers_a_question(normalized_rows, row_index):
            continue
        for hedge_match in _PATIENT_HEDGE_PATTERN.finditer(row["text"]):
            # Only the equivocal-denial shape ships; the general adjacent-hedge
            # sub-pattern missed its precision gate on the development notes
            # and is deferred per the family kill criterion (spec amendments).
            if not _EQUIVOCAL_DENIAL_HEDGE_PATTERN.search(hedge_match.group(0)):
                continue
            # An equivocal denial hedges the negated topic that follows it.
            hedged_tokens = _equivocal_denial_topic_tokens(
                normalized_rows, row_index, hedge_match
            )
            # The claim must itself assert the negative to restate it.
            if not _HEDGE_WINDOW_NEGATION_PATTERN.search(lowered_sentence):
                continue
            if not (claim_tokens & hedged_tokens):
                continue
            # Quoting the patient's own words preserves the hedge honestly.
            if _has_patient_verbatim_quote(sentence, normalized_rows):
                return None
            return (
                "the patient hedged this statement, but the sentence presents it"
                " as definite - preserve the hedge or quote the patient",
                "hedge-dropped",
            )

    return None


def _equivocal_denial_topic_tokens(
    normalized_rows: list[dict[str, str]],
    row_index: int,
    hedge_match: re.Match[str],
) -> set[str]:
    """The negated topic following an equivocal denial hedge.

    "I don't think so ... but no no weight change, just a bit" hedges the
    weight-change denial; the topic is the meaningful tokens right after the
    last consecutive negation in the forward window.
    """
    window_after_hedge = (
        normalized_rows[row_index]["text"][hedge_match.end() :]
        + " "
        + _hedge_row_window(normalized_rows, row_index + 1)
    )
    negation_matches = list(_HEDGE_WINDOW_NEGATION_PATTERN.finditer(window_after_hedge))
    if not negation_matches:
        return set()

    trailing_text = window_after_hedge[negation_matches[-1].end() :]
    topic_tokens = [
        _singular_token(token)
        for token in re.findall(r"[a-z0-9]+", trailing_text.lower())
        if len(token) >= 3 and token not in _HEDGE_CONTENT_STOPWORDS
    ]
    return set(topic_tokens[:3])


def _hedge_row_answers_a_question(
    normalized_rows: list[dict[str, str]], row_index: int
) -> bool:
    """Whether this patient row sits in answer position after a question.

    The hedged row must be within the first patient rows following a DOCTOR
    question row, with no other clinician turn in between.
    """
    for earlier_index in range(row_index - 1, max(-1, row_index - 4), -1):
        earlier_row = normalized_rows[earlier_index]
        if earlier_row["role"] == "DOCTOR":
            return "?" in earlier_row["text"]
        # Anything but the patient's own continuation breaks answer position.
        if earlier_row["role"] != "PATIENT":
            return False
    return False


def _hedge_row_window(normalized_rows: list[dict[str, str]], row_index: int) -> str:
    """The hedge row joined with its neighbors; hedged content splits rows.

    The forward reach is two rows because a self-correction can trail that
    far ("I don't think so." / "... but no no" / "weight change, just a bit").
    """
    window_start = max(0, row_index - 1)
    return " ".join(
        row["text"] for row in normalized_rows[window_start : row_index + 3]
    )


def _hedge_adjacent_tokens(row_text: str, hedge_match: re.Match[str]) -> set[str]:
    """Meaningful tokens immediately around one hedge occurrence."""
    before_tokens = re.findall(r"[a-z0-9]+", row_text[: hedge_match.start()].lower())
    after_tokens = re.findall(r"[a-z0-9]+", row_text[hedge_match.end() :].lower())
    neighborhood = (
        before_tokens[-_HEDGE_ADJACENT_TOKEN_RADIUS:]
        + after_tokens[:_HEDGE_ADJACENT_TOKEN_RADIUS]
    )
    return {
        _singular_token(token)
        for token in neighborhood
        if len(token) >= 3 and token not in _HEDGE_CONTENT_STOPWORDS
    }


# --- Coverage lane: note-level critical-coverage reasons -----------------
# Note-level reasons (reason lane; they also join the single bounded retry via
# regeneration feedback in summary_generation): an answered mental-health
# screen or a spoken emergency-disposition component missing from the whole
# note must surface as review-required, never vanish silently.

MENTAL_HEALTH_SCREEN_MISSING_REASON = "mental_health_screen_missing"
EMERGENCY_DISPOSITION_INCOMPLETE_REASON = "emergency_disposition_incomplete"

# The disposition check runs only in real emergency visits.
_EMERGENCY_CONTEXT_PATTERN = re.compile(r"\b999\b|\b911\b|\bambulance\b", re.IGNORECASE)
# (component, spoken form in DOCTOR windows, note form anywhere in the note)
_EMERGENCY_COVERAGE_COMPONENTS = (
    (
        "emergency_number",
        re.compile(r"\b999\b|\b911\b"),
        re.compile(r"\b999\b|\b911\b|\bemergency (?:number|services)\b", re.IGNORECASE),
    ),
    (
        "ambulance",
        re.compile(r"\bambulance\b", re.IGNORECASE),
        re.compile(r"\bambulance\b", re.IGNORECASE),
    ),
    (
        "stay_with_person",
        re.compile(
            r"\bstay with\b|\bwith you (?:till|until)\b|\bperson'?s with you\b"
            r"|\bmake sure (?:that )?(?:the )?person\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bstay(?:ing)? with\b|\bremain(?:s|ing)? with\b|\bpresent until\b"
            r"|\baccompan",
            re.IGNORECASE,
        ),
    ),
    (
        "interim_medication",
        re.compile(r"\bin the interim\b|\bin the meantime\b", re.IGNORECASE),
        re.compile(
            r"\binterim\b|\bmeantime\b|\bwhile await|\bpending ambulance\b"
            r"|\bif available\b",
            re.IGNORECASE,
        ),
    ),
    (
        "follow_up",
        re.compile(r"\bring back\b|\bcall back\b|\bfollow[- ]?up\b", re.IGNORECASE),
        re.compile(
            r"\bfollow[- ]?up\b|\bcall back\b|\bring back\b|\breview\b",
            re.IGNORECASE,
        ),
    ),
)


def note_coverage_review_reasons(
    sections: list[dict[str, Any]],
    key_points: list[str],
    transcript_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Note-level reasons for spoken critical content the whole note omits.

    Use after generation: these are note-scoped (sentence is empty), feed the
    single bounded retry, and surface as review-required when they survive.

    Args:
        sections: Generated SOAP sections; empty means an empty note, which
            omits everything the source spoke.
        key_points: TL;DR lines joining the note text.
        transcript_rows: The note's selected source rows; empty detects no
            screens or dispositions, so nothing can be honestly demanded.

    Returns:
        Note-level entries: {section: "note", sentence: "", reason, detail,
        segment_ids}; empty means every detected critical item is covered.
    """
    if not transcript_rows:
        return []

    source_rows = [
        {
            "segment_id": str(row.get("segment_id", "")),
            "role": str(row.get("role", "")).upper(),
            "text": str(row.get("text", "") or "").lower(),
        }
        for row in transcript_rows
    ]
    note_text = " ".join(
        [str(section.get("content", "")) for section in sections]
        + [str(key_point) for key_point in key_points]
    )

    review_reasons: list[dict[str, Any]] = []
    review_reasons.extend(_screen_coverage_reasons(source_rows, note_text))
    review_reasons.extend(_disposition_coverage_reasons(source_rows, note_text))
    return review_reasons


def _screen_coverage_reasons(
    source_rows: list[dict[str, str]], note_text: str
) -> list[dict[str, Any]]:
    """An answered risk screen must be represented somewhere in the note."""
    review_reasons: list[dict[str, Any]] = []
    reported_topics: set[str] = set()
    for screen_pair in _risk_screen_pairs(source_rows):
        # Screens without a nameable topic cannot be demanded of the note.
        for topic in screen_pair["topics"] - reported_topics:
            # A claim naming the topic anywhere in the note covers the screen.
            if _RISK_TOPIC_CLAIM_PATTERNS[topic].search(note_text):
                continue
            reported_topics.add(topic)
            review_reasons.append(
                {
                    "section": "note",
                    "sentence": "",
                    "reason": MENTAL_HEALTH_SCREEN_MISSING_REASON,
                    "detail": f"the answered '{topic}' screen is not represented"
                    " anywhere in the note",
                    "segment_ids": screen_pair["segment_ids"][:6],
                }
            )
    return review_reasons


def _disposition_coverage_reasons(
    source_rows: list[dict[str, str]], note_text: str
) -> list[dict[str, Any]]:
    """Every spoken emergency-disposition component must reach the note."""
    # Non-emergency visits have no disposition to demand.
    if not any(
        row["role"] == "DOCTOR" and _EMERGENCY_CONTEXT_PATTERN.search(row["text"])
        for row in source_rows
    ):
        return []

    missing_components: list[str] = []
    evidence_ids: list[str] = []
    for component, spoken_pattern, note_pattern in _EMERGENCY_COVERAGE_COMPONENTS:
        spoken_row_ids = [
            row["segment_id"]
            for row_index, row in enumerate(source_rows)
            # Disposition instructions are clinician speech; split phrases
            # ("in the / interim") are found on joined windows.
            if row["role"] == "DOCTOR"
            and spoken_pattern.search(_window_text(source_rows, row_index, 1))
        ]
        if not spoken_row_ids:
            continue
        if note_pattern.search(note_text):
            continue
        missing_components.append(component)
        evidence_ids.extend(spoken_row_ids[:2])

    if not missing_components:
        return []

    return [
        {
            "section": "note",
            "sentence": "",
            "reason": EMERGENCY_DISPOSITION_INCOMPLETE_REASON,
            "detail": "spoken disposition components missing from the note: "
            + ", ".join(missing_components),
            "segment_ids": evidence_ids[:6],
        }
    ]
