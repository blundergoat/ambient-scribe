"""
Reviewed phrase inventory for the post-visit correction decoder.

The corrected lane can bias decoding toward a small list of clinical terms. This
module owns which terms are allowed to reach that decoder and refuses everything
else, so a phrase can only be boosted after it has been written down, evidenced,
and reviewed.

Every term here was spoken in a captured consultation. Targets are terms the
corrected lane still renders wrongly; controls are terms it already gets right,
carried so a regression is visible rather than silent.

The inventory is inert until an evaluator asks for it. Clinician requests use the
production default, which stays null, so ordinary visits decode exactly as they
did before this module existed.
"""

from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any


INVENTORY_FILENAME = "post_visit_correction_phrases.json"
DEFAULT_INVENTORY_PATH = Path(__file__).with_name("data") / INVENTORY_FILENAME


class CorrectionPhraseInventoryError(ValueError):
    """Raised when the inventory file cannot be trusted to gate a decoder."""


def inventory_path() -> Path:
    """Resolve the inventory file the decoder gate reads.

    Returns:
        Path from `POST_VISIT_CORRECTION_PHRASES_PATH`, or the packaged default.
    """
    configured = os.environ.get("POST_VISIT_CORRECTION_PHRASES_PATH", "").strip()
    return Path(configured) if configured else DEFAULT_INVENTORY_PATH


@lru_cache(maxsize=4)
def _load(path_text: str) -> dict[str, Any]:
    """Read and structurally validate one inventory file.

    Args:
        path_text: Filesystem path as text so the result can be cached.

    Returns:
        Parsed inventory mapping; never partially valid.

    Raises:
        CorrectionPhraseInventoryError: When the file is missing, malformed, or
            declares a phrase that is empty or duplicated.
    """
    path = Path(path_text)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as missing:
        raise CorrectionPhraseInventoryError(
            f"correction phrase inventory not found: {path}"
        ) from missing
    except json.JSONDecodeError as malformed:
        raise CorrectionPhraseInventoryError(
            f"correction phrase inventory is not valid JSON: {path}"
        ) from malformed

    entries = raw.get("phrases")
    # An inventory with no phrase list cannot gate anything, so it fails closed.
    if not isinstance(entries, list) or not entries:
        raise CorrectionPhraseInventoryError("inventory declares no phrases")

    seen: set[str] = set()
    for entry in entries:
        phrase = (entry or {}).get("phrase")
        if not isinstance(phrase, str) or not phrase.strip():
            raise CorrectionPhraseInventoryError("inventory contains an empty phrase")
        # A duplicate would double a term's weight without saying so anywhere.
        if phrase in seen:
            raise CorrectionPhraseInventoryError(
                f"inventory repeats a phrase: {phrase}"
            )
        seen.add(phrase)

    return raw


def load_inventory(path: Path | str | None = None) -> dict[str, Any]:
    """Return the validated inventory mapping.

    Args:
        path: Explicit file for tests; null uses the configured/default path.

    Returns:
        Inventory mapping including phrases, hard negatives, and review state.
    """
    return _load(str(path if path is not None else inventory_path()))


def approved_phrases(path: Path | str | None = None) -> tuple[str, ...]:
    """List every phrase the decoder gate will accept.

    Args:
        path: Explicit file for tests; null uses the configured/default path.

    Returns:
        Phrases in declaration order; empty is impossible because loading fails first.
    """
    return tuple(entry["phrase"] for entry in load_inventory(path)["phrases"])


def phrases_for_role(role: str, path: Path | str | None = None) -> tuple[str, ...]:
    """List phrases carrying one role, such as `target` or `control`.

    Args:
        role: Declared role; an unknown role yields an empty result rather than an error.
        path: Explicit file for tests; null uses the configured/default path.

    Returns:
        Matching phrases in declaration order.
    """
    return tuple(
        entry["phrase"]
        for entry in load_inventory(path)["phrases"]
        if entry.get("role") == role
    )


def hard_negative_terms(path: Path | str | None = None) -> tuple[str, ...]:
    """List terms that must never be produced as a result of boosting.

    Args:
        path: Explicit file for tests; null uses the configured/default path.

    Returns:
        Adversarial terms in declaration order; empty means the inventory declares none.
    """
    return tuple(
        entry["term"]
        for entry in load_inventory(path).get("hard_negatives", [])
        if isinstance(entry, dict) and isinstance(entry.get("term"), str)
    )


def inventory_identity(path: Path | str | None = None) -> dict[str, Any]:
    """Describe which inventory produced a decode, for run evidence.

    A phrase arm is only comparable against another run using the same list, so
    the digest covers the exact accepted phrases rather than the whole file.

    Args:
        path: Explicit file for tests; null uses the configured/default path.

    Returns:
        Inventory id, phrase count, locale, and a sha256 over the sorted phrases.
    """
    inventory = load_inventory(path)
    phrases = approved_phrases(path)
    digest = hashlib.sha256("\n".join(sorted(phrases)).encode("utf-8")).hexdigest()
    return {
        "inventory_id": inventory.get("inventory_id"),
        "locale": inventory.get("locale"),
        "phrase_count": len(phrases),
        "phrases_sha256": digest,
        "clinically_reviewed": bool(
            inventory.get("review", {}).get("clinically_reviewed", False)
        ),
    }
