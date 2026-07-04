"""
Medical lexicon helpers for transcript text shown to clinicians.

True NeMo decode-time phrase boosting still needs GPU-container proof. Until
that is verified, this module provides a conservative toggleable post-ASR
normaliser for common clinical terms that the browser and summaries display.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MedicalPhrase:
    """
    Canonical clinical phrase plus known ASR variants.

    Use this when a visible transcript may contain a spoken drug or condition
    that should be normalised without changing unrelated words.
    """

    canonical: str
    variants: tuple[str, ...]


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

    phrases: list[MedicalPhrase] = []
    # Each non-comment line is one clinician-visible term and optional variants.
    for line in lexicon_path.read_text(encoding="utf-8").splitlines():
        stripped_line = line.strip()
        # Blank/comment lines make the lexicon easier to maintain.
        if not stripped_line or stripped_line.startswith("#"):
            continue

        columns = [
            column.strip() for column in stripped_line.split("|") if column.strip()
        ]
        # A malformed line is ignored so one typo does not break transcription.
        if not columns:
            continue

        canonical = columns[0]
        variants = tuple(dict.fromkeys((canonical, *columns[1:])))
        phrases.append(MedicalPhrase(canonical=canonical, variants=variants))

    return tuple(phrases)


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
    corrected_text = visible_text
    # Each phrase is applied independently so terms can be extended without code edits.
    for phrase in phrases:
        # Variants include the canonical spelling so case can be normalised too.
        for variant in phrase.variants:
            pattern = re.compile(rf"\b{re.escape(variant)}\b", flags=re.IGNORECASE)
            corrected_text = pattern.sub(phrase.canonical, corrected_text)

    return corrected_text
