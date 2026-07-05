"""
Medical lexicon helpers for transcript text shown to clinicians.

True NeMo decode-time phrase boosting still needs GPU-container proof. Until
that is verified, this module provides a conservative toggleable post-ASR
normaliser for common clinical terms that the browser and summaries display.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MedicalPhrase:
    """
    Canonical clinical phrase plus known ASR variants.

    Use this when a visible transcript may contain a spoken drug or condition
    that should be normalised without changing unrelated words.
    """

    canonical: str
    variants: tuple[str, ...]


@dataclass(frozen=True)
class MedicalLexiconMatch:
    """
    One correction decision made inside a transcript row.

    Use this for CPU-only audit and tests when a reviewer needs to see which
    lexicon row changed the visible text, without adding browser payload fields
    or logging the patient's transcript text.

    Attributes:
        canonical: Reviewer-approved phrase shown to the clinician.
        variant: Matched ASR phrase; empty is never emitted by the loader.
        count: Number of matches in one transcript row; zero is not recorded.
    """

    canonical: str
    variant: str
    count: int


@dataclass(frozen=True)
class MedicalCorrectionResult:
    """
    Corrected transcript text plus bounded correction evidence.

    Use when an evaluator or future audit path needs both what the clinician
    sees and which safe lexicon entries fired. Empty matches mean the visible
    transcript stayed raw.
    """

    corrected_text: str
    matches: tuple[MedicalLexiconMatch, ...]


def default_medical_lexicon_path() -> Path:
    """Return the checked-in lexicon path used by the transcription pipeline.

    Returns:
        Path to the default lexicon; missing means correction should stay off.
    """
    return Path(__file__).resolve().parent / "data" / "medical_lexicon.txt"


def load_medical_lexicon(path: str | Path) -> tuple[MedicalPhrase, ...]:
    """Load canonical clinical phrases and ASR variants from disk.

    Args:
        path: Lexicon file path; missing or empty means no correction entries are loaded.

    Returns:
        Ordered phrase entries; empty means the UI transcript is left unchanged.
    """
    lexicon_path = Path(path)
    # A missing lexicon should degrade to raw ASR text, not block recording.
    if not lexicon_path.exists():
        return ()

    try:
        lexicon_lines = lexicon_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        logger.warning(
            "medical_lexicon.unavailable path=%s error_type=%s",
            str(lexicon_path),
            type(error).__name__,
            extra={
                "path": str(lexicon_path),
                "error_type": type(error).__name__,
            },
        )
        return ()

    phrases: list[MedicalPhrase] = []
    seen_canonicals: set[str] = set()
    variant_owner_by_text: dict[str, str] = {}
    # Each non-comment line is one clinician-visible term and optional variants.
    for line_number, line in enumerate(lexicon_lines, start=1):
        phrase = _medical_phrase_from_line(line, line_number, str(lexicon_path))
        # Blank, comment, or invalid rows leave the transcript path usable.
        if phrase is None:
            continue

        canonical_key = phrase.canonical.casefold()
        # Duplicate canonical rows would make reviewer sign-off ambiguous.
        if canonical_key in seen_canonicals:
            logger.warning(
                "medical_lexicon.duplicate_canonical path=%s line=%s canonical=%s",
                str(lexicon_path),
                line_number,
                phrase.canonical,
                extra={
                    "path": str(lexicon_path),
                    "line": line_number,
                    "canonical": phrase.canonical,
                },
            )
            continue

        safe_variants: list[str] = []
        # Each variant must belong to one canonical phrase so replacements stay predictable.
        for variant in phrase.variants:
            variant_key = variant.casefold()
            owner = variant_owner_by_text.get(variant_key)
            # A reused variant could flip the visible clinical term between rows.
            if owner is not None and owner != phrase.canonical:
                logger.warning(
                    "medical_lexicon.variant_collision path=%s line=%s variant=%s owner=%s canonical=%s",
                    str(lexicon_path),
                    line_number,
                    variant,
                    owner,
                    phrase.canonical,
                    extra={
                        "path": str(lexicon_path),
                        "line": line_number,
                        "variant": variant,
                        "owner": owner,
                        "canonical": phrase.canonical,
                    },
                )
                continue

            variant_owner_by_text[variant_key] = phrase.canonical
            safe_variants.append(variant)

        # A row with no usable variants cannot safely alter what the clinician sees.
        if safe_variants == []:
            continue

        seen_canonicals.add(canonical_key)
        phrases.append(
            MedicalPhrase(canonical=phrase.canonical, variants=tuple(safe_variants))
        )

    return tuple(phrases)


def _medical_phrase_from_line(
    line: str,
    line_number: int,
    lexicon_path: str,
) -> MedicalPhrase | None:
    """Parse one editable lexicon row for the visible transcript fallback.

    Args:
        line: Raw lexicon row; blank or comment rows mean no user-visible change.
        line_number: File line number for bounded diagnostics; zero would only affect logs.
        lexicon_path: Lexicon filename for diagnostics; empty still degrades safely.

    Returns:
        Parsed phrase, or `None` when the row should not affect the transcript.
    """
    stripped_line = line.strip()
    # Blank/comment lines make the lexicon easier to review without changing output.
    if stripped_line == "" or stripped_line.startswith("#"):
        return None

    raw_columns = [column.strip() for column in stripped_line.split("|")]
    # An empty canonical term would turn a variant into an unsafe visible diagnosis.
    if raw_columns == [] or raw_columns[0] == "":
        logger.warning(
            "medical_lexicon.empty_canonical path=%s line=%s",
            lexicon_path,
            line_number,
            extra={"path": lexicon_path, "line": line_number},
        )
        return None

    canonical = raw_columns[0]
    variants: list[str] = []
    seen_variant_keys: set[str] = set()
    # Variants are checked in file order so reviewer intent remains visible.
    for column in (canonical, *raw_columns[1:]):
        # Empty variant cells are harmless and leave the visible transcript unchanged.
        if column == "":
            continue

        variant_key = column.casefold()
        # Duplicate variants hide review mistakes, so warn and keep the first spelling.
        if variant_key in seen_variant_keys:
            logger.warning(
                "medical_lexicon.duplicate_variant path=%s line=%s variant=%s",
                lexicon_path,
                line_number,
                column,
                extra={
                    "path": lexicon_path,
                    "line": line_number,
                    "variant": column,
                },
            )
            continue

        seen_variant_keys.add(variant_key)
        variants.append(column)

    # A row with no usable visible text cannot correct anything for the user.
    if variants == []:
        return None

    return MedicalPhrase(canonical=canonical, variants=tuple(variants))


def correct_medical_terms(
    visible_text: str,
    phrases: tuple[MedicalPhrase, ...],
) -> str:
    """Normalise known clinical terms in transcript text.

    Args:
        visible_text: ASR text shown to the clinician; blank remains blank.
        phrases: Lexicon entries; empty leaves the text unchanged.

    Returns:
        Corrected text; unchanged when no exact phrase variant is present.
    """
    return correct_medical_terms_with_audit(visible_text, phrases).corrected_text


def correct_medical_terms_with_audit(
    visible_text: str,
    phrases: tuple[MedicalPhrase, ...],
) -> MedicalCorrectionResult:
    """Normalise clinical terms and keep bounded in-memory match evidence.

    Args:
        visible_text: ASR text shown to the clinician; blank remains blank.
        phrases: Lexicon entries; empty leaves text unchanged and records no matches.

    Returns:
        Corrected text and match counts; empty matches mean no sidebar or summary text changed.
    """
    # Empty text means the browser has no words to correct for this row.
    if visible_text == "":
        return MedicalCorrectionResult(corrected_text="", matches=())

    corrected_text = visible_text
    correction_matches: list[MedicalLexiconMatch] = []
    # Each phrase is applied independently so terms can be extended without code edits.
    for phrase in phrases:
        phrase_pattern = _phrase_pattern(phrase.variants)
        matched_variants: dict[str, int] = {}

        def replace_match(match: re.Match[str]) -> str:
            matched_variant = match.group(0)
            normalized_variant = " ".join(matched_variant.split()).casefold()
            matched_variants[normalized_variant] = (
                matched_variants.get(normalized_variant, 0) + 1
            )
            return _canonical_with_matching_case(phrase.canonical, matched_variant)

        corrected_text = phrase_pattern.sub(replace_match, corrected_text)

        # Only fired variants are retained so audit output stays small.
        for normalized_variant, match_count in matched_variants.items():
            correction_matches.append(
                MedicalLexiconMatch(
                    canonical=phrase.canonical,
                    variant=normalized_variant,
                    count=match_count,
                )
            )

    return MedicalCorrectionResult(
        corrected_text=corrected_text,
        matches=tuple(correction_matches),
    )


def _phrase_pattern(variants: tuple[str, ...]) -> re.Pattern[str]:
    """Compile exact word-boundary matching for one visible clinical phrase.

    Args:
        variants: Known ASR variants; empty would match nothing and should not be passed.

    Returns:
        Regex that matches whole variants with flexible spaces between words.
    """
    variant_patterns = []
    # Longer variants match first so multi-word ASR mistakes stay intact.
    for variant in sorted(variants, key=len, reverse=True):
        variant_patterns.append(_variant_pattern_text(variant))

    return re.compile(
        rf"\b(?:{'|'.join(variant_patterns)})\b",
        flags=re.IGNORECASE,
    )


def _variant_pattern_text(variant: str) -> str:
    """Convert one lexicon variant into a whitespace-tolerant regex fragment.

    Args:
        variant: Lexicon phrase; empty would produce no safe transcript match.

    Returns:
        Regex fragment for one variant; spaces match normal transcript spacing.
    """
    return r"\s+".join(re.escape(part) for part in variant.split())


def _canonical_with_matching_case(canonical: str, matched_variant: str) -> str:
    """Preserve sentence-initial capitalization for corrected transcript text.

    Args:
        canonical: Reviewer-approved visible phrase; empty would stay empty on screen.
        matched_variant: ASR text that matched; empty means no case hint is available.

    Returns:
        Visible phrase with a capital first letter when the user saw one in the ASR text.
    """
    # Empty values are defensive; valid lexicon rows always have text.
    if canonical == "" or matched_variant == "":
        return canonical

    # Sentence-initial terms should not be lowercased in the clinician's transcript.
    if matched_variant[0].isupper():
        return f"{canonical[0].upper()}{canonical[1:]}"

    return canonical
