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
    "patient-unsure",
)

# A note may attribute uncertainty, memory failure, lack of knowledge, or
# refusal to the patient only when the patient's own words establish that
# state for the same clinical topic (M11).
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
# clinical topic. The remaining words reuse M10's stem matcher.
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

# Clinical characteristics whose value must come from the patient, keyed by the
# words a doctor uses when asking. Each entry lists the assertion tokens a note
# would use to (wrongly) resolve the answer.
_CHARACTERISTIC_ASSERTION_TOKENS = {
    "onset": ("sudden", "suddenly", "gradual", "gradually", "abrupt", "abruptly"),
}

# Note phrasings that claim a negative finding the clinician can act on.
_NEGATIVE_FINDING_PATTERN = re.compile(
    r"\b(denies|denied|reports? no|no complaints? of|negative for|not experiencing|"
    r"does not report|without any|no history of)\b\s+(?P<topics>[^.;:]{3,120})",
    re.IGNORECASE,
)

# Patient words that count as an actual denial the note may rely on.
_PATIENT_DENIAL_PATTERN = re.compile(
    r"\b(no|not|nope|never|don'?t|doesn'?t|haven'?t|hasn'?t|none)\b", re.IGNORECASE
)

# Phrases that voice uncertainty or a non-answer. They contain negation words
# ("don't know") but prove the patient could NOT answer - never that they denied
# the topic - so they are masked out before any denial matching (M10).
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
# unanswered contradicts itself; no transcript evidence can rescue it (M10).
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
# performed" also assert absence, not findings (M10).
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
# noun sits under an intent verb, so no findings are being claimed (M10, from
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

# Small stem map so a note's clinical vocabulary matches the patient's words.
_TOPIC_STEM_SYNONYMS = {
    "dyspnea": ("breath",),
    "dyspnoea": ("breath",),
    "breathing": ("breath",),
    "breathless": ("breath",),
    "breathlessness": ("breath",),
    "fever": ("fever", "temperature", "hot"),
    "fevers": ("fever", "temperature", "hot"),
    "pyrexia": ("fever", "temperature"),
    "emesis": ("vomit", "sick"),
    "vomiting": ("vomit", "sick"),
    "nausea": ("nause", "sick"),
    # Headache-radiation paraphrases: notes write "spreading/radiating to other
    # locations", clinicians ask "is it moving anywhere else".
    "spreading": ("spread", "mov", "radiat"),
    "spread": ("spread", "mov", "radiat"),
    "radiating": ("radiat", "mov", "spread"),
    "radiation": ("radiat", "mov", "spread"),
    "moving": ("mov", "spread", "radiat"),
    "location": ("locat", "anywhere", "elsewhere", "area"),
    "locations": ("locat", "anywhere", "elsewhere", "area"),
    "ability": ("abil", "able", "unable"),
    "laziness": ("lazy", "motivat"),
    "motivation": ("motivat", "lazy"),
    "tightness": ("tight",),
}

# Words too generic to identify a denied topic on their own.
_TOPIC_STOPWORDS = frozenset(
    "a an and or of in on at to the any some with for recent formal his her their "
    "difficulty difficulties problem problems issue issues history further other "
    "associated significant when patient screened screening neurological symptoms "
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
    """Build the correction block appended to the prompt for the one retry.

    Use when the first draft failed fidelity checks: the model gets each exact
    sentence and the reason, so the redo fixes the fabrication instead of
    guessing what was wrong.

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
        lines.append(f'- In {violation.location}: "{violation.sentence}" - {violation.reason}.')

    return "\n".join(lines)


def summary_with_unverified_flags(
    parsed_summary: dict[str, Any], violations: list[FidelityViolation]
) -> dict[str, Any]:
    """Mark the sentences that survived regeneration as unverified.

    Use after the single retry still failed: the sentence stays in the note the
    clinician reads, and the browser renders it with an "unverified against
    transcript" marker instead of silently removing it (ADR'd user decision,
    M07).

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
                    flagged_points = parsed_summary.setdefault("unverified_key_points", [])
                    # A duplicate rule must not create duplicate UI markers.
                    if key_point not in flagged_points:
                        flagged_points.append(key_point)

    return parsed_summary


def find_fidelity_violations(
    sections: list[dict[str, Any]],
    key_points: list[str],
    transcript_rows: list[dict[str, Any]],
) -> list[FidelityViolation]:
    """Check every note sentence against the visit transcript.

    Use after citation validation, before the note reaches the browser: the
    result decides whether the note ships clean, regenerates, or renders with
    visible unverified markers.

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
    uncertain_characteristics = _characteristics_answered_with_uncertainty(normalized_rows)

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

    unresolved = _uncertainty_violation(sentence, uncertain_characteristics, normalized_rows)
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

    Use so a genuine quote split across adjacent UI rows still verifies.
    """
    role_token_sequences: list[tuple[str, tuple[str, ...]]] = []
    # No current role exists before the first transcript row is read.
    current_role: str | None = None
    current_role_tokens: list[str] = []
    # Each visible transcript row either extends or closes the current speaker run.
    for row in normalized_rows:
        transcript_role = row["role"]
        # A speaker change prevents a quote from joining two different people.
        if transcript_role != current_role:
            # A non-empty completed run becomes searchable evidence for the note.
            if current_role is not None and current_role_tokens:
                role_token_sequences.append(
                    (current_role, tuple(current_role_tokens))
                )
            current_role = transcript_role
            current_role_tokens = []
        current_role_tokens.extend(_normalized_lexical_tokens(row["text"]))
    # The final non-empty speaker run has no later role change to flush it.
    if current_role is not None and current_role_tokens:
        role_token_sequences.append((current_role, tuple(current_role_tokens)))

    return role_token_sequences


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
    )
    # Clinician mentions before the quote are candidate attributions.
    role_attributions.extend(
        (match.start(), "DOCTOR")
        for match in _DOCTOR_QUOTE_ATTRIBUTION_PATTERN.finditer(note_text_before_quote)
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


def _has_patient_verbatim_quote(sentence: str, normalized_rows: list[dict[str, str]]) -> bool:
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
    trailing_clause = _CLAUSE_BOUNDARY_PATTERN.split(sentence[marker.end() :], maxsplit=1)[0]
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
    return clinical_topic in evidence_text or _matched_topic_words(
        evidence_text, clinical_topic
    ) >= 1


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
        row["role"] == "DOCTOR"
        and _text_supports_topic(row["text"], clinical_topic)
        for row in normalized_rows[last_patient_index + 1 :]
    )


def _recent_clinician_question_context(
    patient_turn_start: int, normalized_rows: list[dict[str, str]]
) -> str:
    """Join bounded clinician fragments leading into the patient's answer.

    Use when the transcript view split one question/answer exchange into rows.
    """
    recent_rows = normalized_rows[
        max(0, patient_turn_start - 12) : patient_turn_start
    ]
    # Only clinician words can supply the question inherited by a short answer.
    return " ".join(
        row["text"] for row in recent_rows if row["role"] == "DOCTOR"
    )


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
        for evidence_marker in _PATIENT_STATE_MARKER_PATTERN.finditer(patient_turn_text):
            # A memory claim cannot borrow support from a different state family.
            if _patient_state_kind(evidence_marker) != patient_state_kind:
                continue
            local_window = _state_marker_local_window(patient_turn_text, evidence_marker)
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
    """Extract every topic a note sentence claims the patient denied.

    Handles the three phrasings notes use: "denies X, Y", "screened for X and Y,
    the patient denied all" (topics before the verb), and "screening (X, Y)
    negative" (parenthetical list).

    Args:
        sentence: Note sentence under review.

    Returns:
        Cleaned topic phrases to verify; empty means the sentence claims no denial.
    """
    match = _NEGATIVE_FINDING_PATTERN.search(sentence)
    # A standard denial phrase exposes its topic text after the denial verb.
    if match is not None:
        topics = _denied_topics(match.group("topics"))
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


def _did_patient_deny_topic(topic: str, normalized_rows: list[dict[str, str]]) -> bool:
    """Report whether the patient actually denied one topic.

    Args:
        topic: Cleaned denied-topic phrase from the note.
        normalized_rows: Lowercased transcript rows in spoken order.

    Returns:
        True when a patient denial covers the topic - inside one local clause,
        or as a short denial answer to a clinician question that named the
        topic just before.
    """
    required_words = _topic_support_requirement(topic)
    # Each patient row is checked for direct or short-answer denial evidence.
    for row_index, row in enumerate(normalized_rows):
        # Clinician rows are questions, never the patient's denial.
        if row["role"] != "PATIENT":
            continue
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
        if _is_short_denial_answer(masked):
            recent_rows = normalized_rows[max(0, row_index - 6) : row_index]
            # Each recent row may hold the clinician topic inherited by "No."
            for earlier in recent_rows:
                # Only clinician words can define the preceding question.
                if earlier["role"] != "DOCTOR":
                    continue
                # One sufficiently specific question verifies the short answer.
                if (
                    topic in earlier["text"]
                    or _matched_topic_words(earlier["text"], topic) >= required_words
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
    # Honest absence ("no examination findings documented") is the wanted behavior.
    if _EXAM_ABSENCE_PATTERN.search(sentence) is not None:
        return None

    # An exam that is only proposed/planned claims no findings either.
    if _EXAM_INTENT_PATTERN.search(sentence) is not None:
        return None

    claims_exam = any(pattern.search(sentence) is not None for pattern in _EXAM_CLAIM_PATTERNS)
    # Sentences without exam language have nothing to prove.
    if not claims_exam:
        return None

    # A transcript row proving a performed exam legitimises the claim.
    for row in normalized_rows:
        # Only a clinician's performed-exam wording can support exam findings.
        if row["role"] == "DOCTOR" and _EXAM_PERFORMANCE_PATTERN.search(row["text"]) is not None:
            return None

    return (
        "this sentence uses examination-findings language, but the transcript shows no"
        " examination was performed - screening answers are history, not exam findings",
        "exam-claim-without-performance",
    )
