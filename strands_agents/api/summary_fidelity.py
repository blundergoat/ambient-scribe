"""
Deterministic fidelity checks for the generated post-visit note (0.4.0 M07).

After the summary agent drafts the SOAP note, these checks compare every
sentence against the visit transcript and catch the fabrication families the
M00 replay campaign proved prompt rules cannot reliably prevent: patient
uncertainty resolved to one side, negative findings the patient never gave,
and screening answers dressed up as examination findings. Violations first
earn one regeneration; survivors are visibly flagged in the note rather than
silently removed, so the clinician always sees what could not be verified.
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

    for violation in violations:
        placed = False
        # The sentence is flagged where the clinician will actually read it.
        for section in parsed_summary.get("sections", []):
            if violation.sentence in str(section.get("content", "")):
                section.setdefault("unverified", [])
                # One flag per sentence keeps repeated rules from stacking markers.
                if violation.sentence not in section["unverified"]:
                    section["unverified"].append(violation.sentence)
                placed = True
        # Key-point lines live outside sections and carry their own flag list.
        if not placed:
            for key_point in parsed_summary.get("key_points", []):
                if violation.sentence in str(key_point):
                    flagged_points = parsed_summary.setdefault("unverified_key_points", [])
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
    for key_point in key_points:
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
    """Run all three fidelity rules over one note sentence.

    Args:
        sentence: One sentence the clinician would read; never empty here.
        location: Section heading or `key_points`, for the flag the user sees.
        normalized_rows: Lowercased transcript rows the sentence must be supported by.
        uncertain_characteristics: Characteristics the patient answered with "I don't know".
        sentence_ordinal: Zero-based position within the location, for PHI-safe logs.

    Returns:
        Violations for this sentence; empty means the sentence is supported.
    """
    found: list[FidelityViolation] = []
    word_count = len(re.findall(r"[A-Za-z'][A-Za-z'-]*", sentence))

    def _record(rule: str, reason: str, subtype: str) -> None:
        found.append(
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
        _record("uncertainty-resolved", *unresolved)

    unsupported_denial = _negative_finding_violation(sentence, normalized_rows)
    # A denial the patient never gave reads as a cleared symptom to the clinician.
    if unsupported_denial is not None:
        _record("negative-without-denial", *unsupported_denial)

    exam_claim = _exam_language_violation(sentence, normalized_rows)
    # Exam findings that never happened carry the most clinical weight of all.
    if exam_claim is not None:
        _record("exam-not-performed", *exam_claim)

    return found


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
        if row["role"] != "DOCTOR":
            continue
        for characteristic, tokens in _CHARACTERISTIC_ASSERTION_TOKENS.items():
            # The doctor must actually have offered the characteristic's options.
            if not any(token in row["text"] for token in tokens):
                continue
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

    return None


def _has_patient_verbatim_quote(sentence: str, normalized_rows: list[dict[str, str]]) -> bool:
    """Report whether the sentence quotes the patient's own words verbatim.

    Args:
        sentence: Note sentence possibly containing a quoted answer.
        normalized_rows: Lowercased transcript rows to match quotes against.

    Returns:
        True when a quoted fragment appears in a patient row - the honest as-stated form.
    """
    quoted_fragments = re.findall(r"[\"'‘’“”]([^\"'‘’“”]{4,80})[\"'‘’“”]", sentence)
    # Each quoted fragment is honest only if the patient actually said it.
    for fragment in quoted_fragments:
        fragment_lower = fragment.lower().strip()
        for row in normalized_rows:
            if row["role"] == "PATIENT" and fragment_lower[:40] in row["text"]:
                return True

    return False


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

    for topic in topics:
        # Every denied topic needs the patient's own "no" somewhere near it.
        if not _did_patient_deny_topic(topic, normalized_rows):
            return (
                f"the transcript contains no patient denial of '{topic}' - an unanswered"
                " clinician question is not a denial",
                "no-local-topic-support",
            )

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
    if not words or words[0] not in _ANSWER_DENIAL_OPENERS:
        return False
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
    for row_index, row in enumerate(normalized_rows):
        if row["role"] != "PATIENT":
            continue
        masked = _mask_epistemic_phrases(row["text"])

        # Direct support: a real denial and the topic inside the SAME clause,
        # so a lip mention early in a monologue cannot pair with a "no" at its
        # end (the day3 laundering hole).
        for clause in _denial_evidence_clauses(masked):
            if _PATIENT_DENIAL_PATTERN.search(clause) is None:
                continue
            if topic in clause or _matched_topic_words(clause, topic) >= required_words:
                return True

        # Short-answer support: "No." counts when a CLINICIAN row named the
        # topic shortly before - six rows back, because live transcripts split
        # one question across several fragment rows.
        if _is_short_denial_answer(masked):
            recent_rows = normalized_rows[max(0, row_index - 6) : row_index]
            for earlier in recent_rows:
                if earlier["role"] != "DOCTOR":
                    continue
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
        if row["role"] == "DOCTOR" and _EXAM_PERFORMANCE_PATTERN.search(row["text"]) is not None:
            return None

    return (
        "this sentence uses examination-findings language, but the transcript shows no"
        " examination was performed - screening answers are history, not exam findings",
        "exam-claim-without-performance",
    )
