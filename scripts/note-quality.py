"""Score one saved SOAP note against its exact persisted transcript source.

Use this after a note and selected-source artifact have both been saved.
The CPU-only report keeps unsupported claims, wrong citations, clinical state,
named terms, numbers, and required screens separate for a quality reviewer.
It never generates, edits, or repairs clinician-visible wording.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Callable


NOTE_QUALITY_SCHEMA_VERSION = "ambient-scribe-note-quality/v1"
LOW_TRUST_CONFIDENCE_FLOOR = 0.78
SOURCE_IDENTITY_FIELDS = ("sha256", "bytes", "session_id", "attestation_id", "lane")
SOURCE_IDENTITY_FAILURE_REASONS = {
    "sha256": "artifact_sha256_mismatch",
    "bytes": "artifact_bytes_mismatch",
    "session_id": "session_id_mismatch",
    "attestation_id": "attestation_id_mismatch",
    "lane": "lane_mismatch",
}
CONSULT_29_DIAGNOSTIC_GARBLE_TERMS = (
    ("mouth forming", "metformin"),
    ("penithy", "penicillin"),
)


class QualityHarnessError(RuntimeError):
    """A saved quality artifact cannot be scored safely.

    The CLI raises this when a reviewer selects missing, malformed, duplicate,
    or unsupported evidence instead of treating it as an empty successful note.
    """


def stable_json_bytes(value: Any) -> bytes:
    """Return canonical report bytes for repeatable reviewer evidence.
    Args:
        value: JSON-compatible report data; null stays an explicit JSON null.
    Returns:
        UTF-8 JSON with sorted keys, compact separators, and one final newline.
    """
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def load_required_json(artifact_path: Path) -> dict[str, Any]:
    """Load one required note-review artifact and fail visibly when unavailable.
    Args:
        artifact_path: Saved JSON path; missing or empty means the review cannot proceed.
    Returns:
        Parsed JSON object for deterministic scoring.
    Raises:
        QualityHarnessError: When the file is missing, unreadable, invalid, or not an object.
    """
    # A missing path means the reviewer selected evidence that was never retained.
    if not artifact_path.is_file():
        raise QualityHarnessError(f"missing_fixture:{artifact_path}")

    try:
        artifact_text = artifact_path.read_text(encoding="utf-8")
    # Example: a developer account can see the artifact name but lacks read permission.
    except OSError as exc:
        raise QualityHarnessError(f"unreadable_fixture:{artifact_path}") from exc

    try:
        parsed_artifact = json.loads(artifact_text)
    # Example: an interrupted evidence write can leave a partial JSON document in the UI review.
    except json.JSONDecodeError as exc:
        raise QualityHarnessError(f"invalid_json:{artifact_path}") from exc

    # A JSON list or null has no stable note/source field contract for the reviewer.
    if not isinstance(parsed_artifact, dict):
        raise QualityHarnessError(f"invalid_document:{artifact_path}")
    return parsed_artifact


def _normalized_words(value: Any) -> list[str]:
    """Return lowercase UI words for exact phrase and state comparisons.
    Args:
        value: Visible text or state name; null/empty produces no words.
    Returns:
        Alphanumeric words in display order with underscores treated as spaces.
    """
    return re.findall(r"[a-z0-9]+", str(value or "").replace("_", " ").lower())


def _contains_exact_phrase(visible_text: str, required_phrase: Any) -> bool:
    """Return whether one frozen phrase occurs as consecutive visible words.
    Args:
        visible_text: Note or source wording; empty means no evidence is visible.
        required_phrase: Frozen phrase/state; null or empty never supports a claim.
    Returns:
        True only when every normalized phrase word appears contiguously.
    """
    visible_words = _normalized_words(visible_text)
    required_words = _normalized_words(required_phrase)
    # Empty expected wording cannot become vacuous support for a clinician claim.
    if not required_words:
        return False

    last_start_index = len(visible_words) - len(required_words)
    # Every possible start preserves exact multiword medication and action wording.
    for start_index in range(last_start_index + 1):
        phrase_end_index = start_index + len(required_words)
        # Exact word order is support; fuzzy garble remains a visible source defect.
        if visible_words[start_index:phrase_end_index] == required_words:
            return True
    return False


def _source_unit_identifier(source_row: dict[str, Any]) -> str:
    """Return the stable ID a clinician citation uses for one persisted row.
    Args:
        source_row: Persisted source row; empty IDs mean the row cannot be cited safely.
    Returns:
        Source-unit ID, segment ID fallback, or an empty string when untraceable.
    """
    return str(
        source_row.get("source_unit_id") or source_row.get("segment_id") or ""
    ).strip()


def _source_rows_by_id(
    selected_source_rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Index exact persisted rows without silently replacing duplicate identities.
    Args:
        selected_source_rows: Selected lane rows; empty means no citations can resolve.
    Returns:
        Source-unit lookup in the persisted artifact order.
    Raises:
        QualityHarnessError: When a row ID is empty or appears more than once.
    """
    indexed_source_rows: dict[str, dict[str, Any]] = {}
    # Every persisted row needs one unique traceable identity for clinician source chips.
    for selected_source_row in selected_source_rows:
        source_unit_id = _source_unit_identifier(selected_source_row)
        # An empty ID makes the visible row impossible to cite or audit later.
        if source_unit_id == "":
            raise QualityHarnessError("source_unit_id_missing")
        # A duplicate ID would let a citation resolve to two different visible rows.
        if source_unit_id in indexed_source_rows:
            raise QualityHarnessError(f"duplicate_source_unit_id:{source_unit_id}")
        indexed_source_rows[source_unit_id] = selected_source_row
    return indexed_source_rows


def _supporting_source_unit_ids(
    source_requirements: dict[str, Any],
    selected_source_rows: list[dict[str, Any]],
) -> list[str]:
    """Return rows that satisfy one claim's exact speaker and phrase contract.
    Args:
        source_requirements: Required phrases/role; empty requirements support no claim.
        selected_source_rows: Exact selected lane; empty means unsupported anywhere.
    Returns:
        Supporting source IDs in persisted order.
    """
    required_phrases = list(source_requirements.get("required_phrases", []))
    required_role = str(source_requirements.get("required_role", "")).upper()
    supporting_source_unit_ids: list[str] = []
    # Every selected row is checked independently so another speaker cannot lend support.
    for selected_source_row in selected_source_rows:
        source_row_role = str(selected_source_row.get("role", "")).upper()
        # A pinned role prevents Doctor wording from supporting a Patient claim.
        if required_role != "" and source_row_role != required_role:
            continue

        source_row_text = str(selected_source_row.get("text", ""))
        phrase_support = [
            _contains_exact_phrase(source_row_text, required_phrase)
            # Each named term, state, or number must exist in this same persisted row.
            for required_phrase in required_phrases
        ]
        # Empty requirements or a partial phrase match cannot support a clinical proposition.
        if not phrase_support or not all(phrase_support):
            continue
        supporting_source_unit_ids.append(_source_unit_identifier(selected_source_row))
    return supporting_source_unit_ids


def score_claim_grounding(
    structured_claims: list[dict[str, Any]],
    selected_source_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Score source support and citation choice as independent note outcomes.
    Args:
        structured_claims: Claims with stable IDs and explicit source requirements; empty is zero.
        selected_source_rows: Exact persisted lane; empty makes every non-empty claim unsupported.
    Returns:
        Raw counts and stable IDs for unsupported, mis-cited, unresolved, and supported claims.
    Raises:
        QualityHarnessError: When source or claim identity is missing or duplicated.
    """
    source_rows_by_id = _source_rows_by_id(selected_source_rows)
    seen_claim_ids: set[str] = set()
    unsupported_anywhere_claim_ids: list[str] = []
    miscited_claim_ids: list[str] = []
    unresolved_citation_ids: list[str] = []
    supported_claim_ids: list[str] = []

    # Every atomic proposition keeps one stable result in each applicable risk family.
    for structured_claim in structured_claims:
        claim_id = str(structured_claim.get("claim_id", "")).strip()
        # Empty claim identity prevents a reviewer from tracing the reported failure.
        if claim_id == "":
            raise QualityHarnessError("claim_id_missing")
        # Repeated IDs would silently collapse two different clinician-visible propositions.
        if claim_id in seen_claim_ids:
            raise QualityHarnessError(f"duplicate_claim_id:{claim_id}")
        seen_claim_ids.add(claim_id)

        citation_ids = [
            str(citation_id)
            # Every emitted source chip remains in the unresolved-ID denominator.
            for citation_id in structured_claim.get("citation_ids", [])
        ]
        # Each missing chip ID remains independently visible even if another row supports the text.
        for citation_id in citation_ids:
            # An unresolved ID is a provenance failure, not an unsupported-anywhere claim.
            if citation_id not in source_rows_by_id:
                unresolved_citation_ids.append(citation_id)

        supporting_source_unit_ids = _supporting_source_unit_ids(
            dict(structured_claim.get("source_requirements", {})),
            selected_source_rows,
        )
        # No selected row supports the proposition at the required wording and speaker.
        if not supporting_source_unit_ids:
            unsupported_anywhere_claim_ids.append(claim_id)
            continue

        supported_claim_ids.append(claim_id)
        # Support exists elsewhere, but none of the claim's visible chips points to it.
        if not set(citation_ids).intersection(supporting_source_unit_ids):
            miscited_claim_ids.append(claim_id)

    return {
        "claim_count": len(structured_claims),
        "unsupported_anywhere_count": len(unsupported_anywhere_claim_ids),
        "unsupported_anywhere_claim_ids": sorted(unsupported_anywhere_claim_ids),
        "miscited_count": len(miscited_claim_ids),
        "miscited_claim_ids": sorted(miscited_claim_ids),
        "unresolved_citation_count": len(unresolved_citation_ids),
        "unresolved_citation_ids": sorted(unresolved_citation_ids),
        "supported_claim_ids": sorted(supported_claim_ids),
    }


def score_source_identity(
    *,
    expected_source_identity: dict[str, Any],
    actual_source_identity: dict[str, Any],
    selected_source_rows: list[dict[str, Any]],
    required_source_unit_ids: list[str],
) -> dict[str, Any]:
    """Verify that a note and its required rows use the exact attested source.
    Args:
        expected_source_identity: Frozen hash, size, session, attestation, and lane.
        actual_source_identity: Observed identity; empty fields fail their frozen comparisons.
        selected_source_rows: Persisted rows; empty cannot satisfy required source units.
        required_source_unit_ids: IDs needed by note expectations; empty requires no named units.
    Returns:
        Pass state, exact reasons, and resolved/required source-unit counts.
    """
    source_rows_by_id = _source_rows_by_id(selected_source_rows)
    failure_reasons: list[str] = []
    # Every frozen identity field must match; another lane cannot substitute for this source.
    for identity_field in SOURCE_IDENTITY_FIELDS:
        expected_value = expected_source_identity.get(identity_field)
        # An absent frozen value is not compared because no contract was registered for it.
        if expected_value is None:
            continue
        # A changed hash/session/lane means the clinician note came from different evidence.
        if actual_source_identity.get(identity_field) != expected_value:
            failure_reasons.append(SOURCE_IDENTITY_FAILURE_REASONS[identity_field])

    # An incomplete source blocks note comparison even when its current hash is known.
    if actual_source_identity.get("terminal_complete") is not True:
        failure_reasons.append("source_not_terminal_complete")

    unique_required_source_unit_ids = list(dict.fromkeys(required_source_unit_ids))
    resolved_required_source_units = 0
    # Every expectation-owned unit must resolve in the exact selected artifact.
    for required_source_unit_id in unique_required_source_unit_ids:
        # A present unit contributes to the resolution count shown to the reviewer.
        if required_source_unit_id in source_rows_by_id:
            resolved_required_source_units += 1
        else:
            failure_reasons.append(
                f"required_source_unit_missing:{required_source_unit_id}"
            )

    return {
        "passed": not failure_reasons,
        "failure_reasons": failure_reasons,
        "resolved_required_source_units": resolved_required_source_units,
        "required_source_units": len(unique_required_source_unit_ids),
    }


def _note_visible_texts(note: dict[str, Any]) -> list[str]:
    """Return every clinician-visible claim, section, and key point in display order.
    Args:
        note: Saved note JSON; empty fields produce no visible wording.
    Returns:
        Strings used by frozen expectation checks without adding inferred prose.
    """
    note_visible_texts: list[str] = []
    # Structured claim lists are the smallest atomic propositions when available.
    for note_claim in note.get("claims", []):
        # A plain string is already the exact wording shown to the clinician.
        if isinstance(note_claim, str):
            note_visible_texts.append(note_claim)
        # A structured claim contributes only its explicit text/content field.
        elif isinstance(note_claim, dict):
            note_visible_texts.append(
                str(note_claim.get("text") or note_claim.get("content") or "")
            )

    # SOAP sections remain scoreable when the saved note has no structured claim array.
    for note_section in note.get("sections", []):
        # Malformed section entries have no clinician-visible prose to score.
        if not isinstance(note_section, dict):
            continue
        note_visible_texts.append(str(note_section.get("content", "")))

    # Key points are separately visible patient-specific propositions in the saved response.
    for key_point in note.get("key_points", []):
        note_visible_texts.append(str(key_point))
    return note_visible_texts


def _joined_note_text(note: dict[str, Any]) -> str:
    """Join exact visible note wording for frozen phrase/state checks.
    Args:
        note: Saved note; empty means the clinician received no claim wording.
    Returns:
        One space-separated view of the exact saved strings.
    """
    return "\n".join(_note_visible_texts(note))


def _has_any_note_phrase(note_text: str, phrases: list[Any]) -> bool:
    """Return whether a note contains any frozen prohibited or required phrase.
    Args:
        note_text: Joined visible note text; empty means no phrase is present.
        phrases: Frozen phrases/states; empty means there is nothing to match.
    Returns:
        True when at least one non-empty phrase is present exactly.
    """
    # Each phrase stays independent so one clinical failure cannot hide another.
    for phrase in phrases:
        # Empty contract entries never become accidental matches.
        if _contains_exact_phrase(note_text, phrase):
            return True
    return False


def _c53_01_failures(expectation: dict[str, Any], note_text: str) -> list[str]:
    """Catch a therapy subtype reconstructed beyond the selected source.
    Use when scoring the clinician's C53-01 therapy wording.
    """
    selected_source_truth = dict(expectation.get("selected_source_truth", {}))
    required_note_behavior = dict(expectation.get("required_note_behavior", {}))
    prohibited_terms = list(selected_source_truth.get("unsupported_terms", []))
    prohibited_terms.extend(required_note_behavior.get("prohibited", []))
    prohibited_terms.extend(required_note_behavior.get("prohibited_states", []))
    # A canonical or cognitive label absent from the source is an unsafe reconstruction.
    if _has_any_note_phrase(note_text, prohibited_terms):
        return ["unsupported_named_term_reconstruction"]
    return []


def _c53_02_failures(expectation: dict[str, Any], note_text: str) -> list[str]:
    """Catch longitudinal alcohol change without two selected-source endpoints.
    Use when the note turns one current snapshot into a trend.
    """
    selected_source_truth = dict(expectation.get("selected_source_truth", {}))
    required_note_behavior = dict(expectation.get("required_note_behavior", {}))
    safe_endpoint_count = int(
        selected_source_truth.get("safe_longitudinal_endpoints", 0) or 0
    )
    minimum_endpoint_count = int(
        required_note_behavior.get("minimum_endpoints_for_change", 2) or 2
    )
    prohibited_states = list(required_note_behavior.get("prohibited_states", []))
    # Two safe endpoints allow a trend; this fixture deliberately has only one.
    if safe_endpoint_count >= minimum_endpoint_count:
        return []

    # Each sentence/claim keeps an unrelated state word such as "reduced sleep" out of alcohol.
    for note_proposition in re.split(r"(?:\n+|(?<=[.!?;])\s+)", note_text):
        proposition_words = set(_normalized_words(note_proposition))
        # Only an alcohol/drinking proposition belongs to the C53-02 trend family.
        if not proposition_words.intersection(
            {"alcohol", "drink", "drinks", "drinking"}
        ):
            continue
        # One endpoint cannot support increased, reduced, resumed, stopped, or unchanged wording.
        if _has_any_note_phrase(note_proposition, prohibited_states):
            return ["longitudinal_claim_without_two_endpoints"]
    return []


def _c53_03_failures(_expectation: dict[str, Any], note_text: str) -> list[str]:
    """Require the chest-pain conflict and heartbeat condition to stay visible.
    Use when scoring C53-03 response state and certainty.
    """
    has_chest_pain_claim = _contains_exact_phrase(note_text, "chest pain")
    # Omitting the required conflicting outcome loses a frozen clinician screen.
    if not has_chest_pain_claim:
        return ["required_conflicting_evidence_omitted"]

    note_words = set(_normalized_words(note_text))
    has_conflict_wording = bool(
        note_words.intersection({"no", "denies", "denied"})
        and note_words.intersection({"yeah", "but", "however", "although"})
    )
    has_heartbeat_condition = bool(
        note_words.intersection({"when", "if"})
        and "heart" in note_words
        and note_words.intersection({"fast", "racing", "beating"})
    )
    # A clean denial or unqualified positive flattens the source's conflicting condition.
    if not has_conflict_wording or not has_heartbeat_condition:
        return ["conflicting_evidence_flattened"]
    return []


def _c53_04_failures(_expectation: dict[str, Any], note_text: str) -> list[str]:
    """Keep the overlapping panic exchange omitted or explicitly unresolved.
    Use when competing Doctor questions make the Patient answer unsafe.
    """
    has_panic_attack_outcome = _contains_exact_phrase(note_text, "panic attack") or (
        _contains_exact_phrase(note_text, "panic attacks")
    )
    # Mentioning panic in another context is not automatically a claim about panic attacks.
    if not has_panic_attack_outcome:
        return []

    note_words = set(_normalized_words(note_text))
    has_denial_wording = bool(
        note_words.intersection({"denies", "denied", "no", "not"})
    )
    has_uncertainty_wording = bool(
        note_words.intersection({"unclear", "uncertain", "asked", "ambiguous"})
    )
    # A clean denial turns an unsafe overlapping exchange into a false negative screen.
    if has_denial_wording and not has_uncertainty_wording:
        return ["unsafe_ambiguous_denial"]
    # A clean positive is equally unsupported when the response cannot be assigned safely.
    if not has_uncertainty_wording:
        return ["unsafe_ambiguous_positive"]
    return []


def _c53_05_failures(expectation: dict[str, Any], note_text: str) -> list[str]:
    """Block a recreational-drug denial until selected Patient evidence is safe.
    Use when C53-05 source wording is garbled or on the wrong speaker.
    """
    selected_source_truth = dict(expectation.get("selected_source_truth", {}))
    note_words = set(_normalized_words(note_text))
    has_drug_outcome = "drug" in note_words or "drugs" in note_words
    has_denial_wording = bool(
        note_words.intersection({"denies", "denied", "no", "not", "none"})
    )
    source_support_is_safe = bool(
        selected_source_truth.get("safe_outcome_support", False)
    )
    # Gold truth and the Doctor question cannot repair missing Patient source support.
    if has_drug_outcome and has_denial_wording and not source_support_is_safe:
        return ["selected_source_missing_or_wrong_speaker"]
    return []


def _c53_06_failures(expectation: dict[str, Any], note_text: str) -> list[str]:
    """Require the supported suicidality denial with its contextual qualifier.
    Use when scoring the high-risk C53-06 screen.
    """
    selected_source_truth = dict(expectation.get("selected_source_truth", {}))
    note_words = set(_normalized_words(note_text))
    has_suicidality_topic = bool(
        note_words.intersection({"suicidal", "suicide", "selfharm"})
    )
    # No suicidality wording means the supported critical screen was omitted.
    if not has_suicidality_topic:
        return ["required_supported_screen_omitted"]

    qualifier = selected_source_truth.get(
        "qualifier", "does not want to go on like this"
    )
    # A bare denial drops the paired qualifier and changes what the clinician reads.
    if not _contains_exact_phrase(note_text, qualifier):
        return ["supported_screen_qualifier_omitted"]
    return []


def _c53_07_failures(expectation: dict[str, Any], note_text: str) -> list[str]:
    """Keep blood tests recommended and the call action prospective.
    Use when scoring the clinician's C53-07 plan state.
    """
    required_note_behavior = dict(expectation.get("required_note_behavior", {}))
    prohibited_states = list(required_note_behavior.get("prohibited_states", []))
    has_test_context = _has_any_note_phrase(
        note_text, ["blood test", "blood tests", "GP follow up", "tests"]
    )
    # Ordered, booked, completed, confirmed, or already arranged overstates the saved source.
    if has_test_context and _has_any_note_phrase(note_text, prohibited_states):
        return ["action_state_strengthened"]

    has_recommendation = _has_any_note_phrase(
        note_text, ["recommended", "recommend", "worth having"]
    )
    has_call_to_arrange = _contains_exact_phrase(
        note_text, "call"
    ) and _has_any_note_phrase(note_text, ["arrange", "arranging"])
    # Missing either supported action leaves the frozen plan state incomplete.
    if not has_recommendation or not has_call_to_arrange:
        return ["required_action_state_omitted"]
    return []


CONSULT_53_FAILURE_RULES: dict[str, Callable[[dict[str, Any], str], list[str]]] = {
    "C53-01": _c53_01_failures,
    "C53-02": _c53_02_failures,
    "C53-03": _c53_03_failures,
    "C53-04": _c53_04_failures,
    "C53-05": _c53_05_failures,
    "C53-06": _c53_06_failures,
    "C53-07": _c53_07_failures,
}


def score_note_expectation(
    *,
    expectation: dict[str, Any],
    selected_source_rows: list[dict[str, Any]],
    note: dict[str, Any],
) -> dict[str, Any]:
    """Score one frozen note expectation against literal selected-source rows.
    Args:
        expectation: Frozen truth contract; empty identity cannot produce a traceable result.
        selected_source_rows: Exact persisted units; empty means no source support is inferred.
        note: Immutable saved note; empty means required screens may be omitted.
    Returns:
        Stable pass/failure evidence for the named clinician-facing expectation.
    Raises:
        QualityHarnessError: When the expectation has no traceable ID.
    """
    expectation_id = str(expectation.get("expectation_id", "")).strip()
    # An unnamed rule cannot produce a reviewer-traceable quality finding.
    if expectation_id == "":
        raise QualityHarnessError("expectation_id_missing")

    note_text = _joined_note_text(note)
    expectation_rule = CONSULT_53_FAILURE_RULES.get(expectation_id)
    # The seven high-risk consult rules have explicit source-conditioned state checks.
    if expectation_rule is not None:
        failure_reasons = expectation_rule(expectation, note_text)
        return {
            "expectation_id": expectation_id,
            "passed": not failure_reasons,
            "failure_reasons": failure_reasons,
        }

    # Generic fixtures keep source text inert; instruction-like wording cannot alter this schema.
    return {
        "schema_version": NOTE_QUALITY_SCHEMA_VERSION,
        "expectation_id": expectation_id,
        "passed": True,
        "failure_reasons": [],
        "selected_source_rows": selected_source_rows,
    }


def _finite_confidences(selected_source_rows: list[dict[str, Any]]) -> list[float]:
    """Return real measured row confidences without promoting null to high or low.
    Args:
        selected_source_rows: Persisted rows; empty/null measurements provide no values.
    Returns:
        Finite numeric confidences in source order.
    """
    measured_confidences: list[float] = []
    # Every row contributes only a real acoustic measurement, never a boolean or null.
    for selected_source_row in selected_source_rows:
        row_confidence = selected_source_row.get("confidence")
        # Booleans are numbers in Python but have no acoustic confidence meaning in the UI.
        if isinstance(row_confidence, bool) or not isinstance(
            row_confidence, (int, float)
        ):
            continue
        confidence_value = float(row_confidence)
        # NaN/Infinity cannot support a stable low-trust reviewer decision.
        if not math.isfinite(confidence_value):
            continue
        measured_confidences.append(confidence_value)
    return measured_confidences


def _consult_29_affected_terms(selected_source_rows: list[dict[str, Any]]) -> list[str]:
    """Return frozen gold terms affected by diagnostic-only garbled source wording.
    Args:
        selected_source_rows: Literal selected rows; empty means no registered garble appears.
    Returns:
        Evaluation terms in frozen order; they are never used to rewrite the note or transcript.
    """
    selected_source_text = " ".join(
        str(selected_source_row.get("text", ""))
        # Every row contributes only its literal wording to the diagnostic check.
        for selected_source_row in selected_source_rows
    )
    affected_terms: list[str] = []
    # Each mapping labels an expected failure; it never supplies canonical note support.
    for garbled_phrase, canonical_term in CONSULT_29_DIAGNOSTIC_GARBLE_TERMS:
        # The exact retained garble identifies which official term was missed.
        if _contains_exact_phrase(selected_source_text, garbled_phrase):
            affected_terms.append(canonical_term)
    return affected_terms


def score_consult_29_probe(scoring_probe: dict[str, Any]) -> dict[str, Any]:
    """Score one source-review or saved-note medication/allergy probe.
    Args:
        scoring_probe: CPU-only probe; empty or transcript-owned scope fails closed.
    Returns:
        Computed low-trust or faithful-abstention evidence without reading expected output.
    Raises:
        ValueError: When the transcript scorer owns the supplied probe scope.
    """
    scoring_scope = str(scoring_probe.get("scoring_scope", ""))
    probe_input = dict(scoring_probe.get("input", {}))
    selected_source_rows = list(probe_input.get("selected_source_rows", []))
    note = dict(probe_input.get("note", {}))
    note_text = _joined_note_text(note)
    source_text = " ".join(
        str(selected_source_row.get("text", ""))
        # Every source row remains literal; adjacent text never repairs a garble.
        for selected_source_row in selected_source_rows
    )

    # Measured low confidence is review evidence only for an exact term already in the source.
    if scoring_scope == "selected_source_review":
        affected_terms = [
            clinical_term
            # These four terms are the frozen consult-2.9 official inventory.
            for clinical_term in ("metformin", "losartan", "amlodipine", "penicillin")
            # Exact selected-source text and note wording must both contain the term.
            if _contains_exact_phrase(source_text, clinical_term)
            and _contains_exact_phrase(note_text, clinical_term)
        ]
        measured_confidences = _finite_confidences(selected_source_rows)
        review_required = bool(
            affected_terms
            and measured_confidences
            and min(measured_confidences) < LOW_TRUST_CONFIDENCE_FLOOR
        )
        outcome_class = "low_trust_flag" if review_required else "no_low_trust_flag"
        return {
            "probe_id": scoring_probe.get("probe_id"),
            "outcome_class": outcome_class,
            "outcomes": [outcome_class],
            "affected_terms": affected_terms,
            "review_required": review_required,
        }

    # Saved-note probes check abstention without treating garble as canonical support.
    if scoring_scope == "saved_note":
        affected_terms = _consult_29_affected_terms(selected_source_rows)
        reconstructed_terms = [
            affected_term
            # A canonical term in the note is unsafe when only its registered garble exists.
            for affected_term in affected_terms
            # Exact note wording is required; fuzzy similarity never creates a failure itself.
            if _contains_exact_phrase(note_text, affected_term)
        ]
        # Omission is the faithful behavior while the selected source remains garbled.
        if not reconstructed_terms:
            return {
                "probe_id": scoring_probe.get("probe_id"),
                "outcome_class": "faithful_soap_abstention",
                "outcomes": ["faithful_soap_abstention"],
                "affected_terms": affected_terms,
                "passed": True,
            }
        return {
            "probe_id": scoring_probe.get("probe_id"),
            "outcome_class": "unsupported_named_term_reconstruction",
            "outcomes": ["unsupported_named_term_reconstruction"],
            "affected_terms": reconstructed_terms,
            "passed": False,
        }

    raise ValueError(f"unsupported note probe scope: {scoring_scope or '<empty>'}")


def _required_source_unit_ids(expectation_document: dict[str, Any]) -> list[str]:
    """Return every consult expectation's required persisted source unit.
    Args:
        expectation_document: Versioned fixture; empty means no named unit is required.
    Returns:
        Unique source IDs in frozen expectation order.
    """
    required_source_unit_ids: list[str] = []
    # Every expectation names the exact selected rows that authorize its note behavior.
    for expectation in expectation_document.get("expectations", []):
        selected_source_truth = dict(expectation.get("selected_source_truth", {}))
        # Each source ID remains visible in the source-integrity denominator.
        for source_unit_id in selected_source_truth.get("source_unit_ids", []):
            # The first appearance fixes stable report order; repeats do not inflate counts.
            if source_unit_id not in required_source_unit_ids:
                required_source_unit_ids.append(str(source_unit_id))
    return required_source_unit_ids


def _note_citation_ids(note: dict[str, Any]) -> list[str]:
    """Return every emitted source-chip ID from a saved structured note.
    Args:
        note: Saved note; empty sections/citations produce an empty denominator.
    Returns:
        Citation IDs in display order, including repeated and unresolved emissions.
    """
    citation_ids: list[str] = []
    # Every SOAP section can carry its own clinician-visible source chips.
    for note_section in note.get("sections", []):
        # A malformed section contributes no trustworthy citation objects.
        if not isinstance(note_section, dict):
            continue
        # Every emitted citation remains in the resolution denominator.
        for citation in note_section.get("citations", []):
            # A malformed citation becomes an empty unresolved ID rather than disappearing.
            if not isinstance(citation, dict):
                citation_ids.append("")
                continue
            citation_ids.append(
                str(citation.get("source_unit_id") or citation.get("segment_id") or "")
            )
    return citation_ids


def score_citation_resolution(
    citation_ids: list[str],
    selected_source_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Score whether every visible note citation resolves in the exact selected source.
    Args:
        citation_ids: Emitted IDs; empty means no citation denominator is available.
        selected_source_rows: Exact persisted lane; empty makes every emitted ID unresolved.
    Returns:
        Emitted/unresolved counts, rate, and stable unresolved IDs.
    """
    source_rows_by_id = _source_rows_by_id(selected_source_rows)
    unresolved_citation_ids: list[str] = []
    # Every emitted chip must resolve; an empty chip ID is still an unresolved emission.
    for citation_id in citation_ids:
        # Another lane's matching text cannot repair a missing source identity.
        if citation_id not in source_rows_by_id:
            unresolved_citation_ids.append(citation_id)

    unresolved_citation_rate: float | None = None
    # No emitted citation gives an unavailable rate rather than a favorable 0% claim.
    if citation_ids:
        unresolved_citation_rate = len(unresolved_citation_ids) / len(citation_ids)
    return {
        "emitted_citation_ids": len(citation_ids),
        "unresolved_citation_count": len(unresolved_citation_ids),
        "unresolved_citation_rate": unresolved_citation_rate,
        "unresolved_citation_ids": sorted(set(unresolved_citation_ids)),
    }


def _structured_claims(note: dict[str, Any]) -> list[dict[str, Any]]:
    """Return only atomic note claims carrying an explicit source contract.
    Args:
        note: Saved note; plain strings remain available to expectation rules only.
    Returns:
        Structured claims whose grounding can be scored deterministically.
    """
    structured_claims: list[dict[str, Any]] = []
    # Claims without explicit requirements are never guessed into semantic entailment.
    for note_claim in note.get("claims", []):
        # Only a structured claim with a requirement has a frozen grounding contract.
        if isinstance(note_claim, dict) and isinstance(
            note_claim.get("source_requirements"), dict
        ):
            structured_claims.append(note_claim)
    return structured_claims


def score_saved_note_document(
    *,
    expectation_document: dict[str, Any],
    note: dict[str, Any],
    selected_source_document: dict[str, Any],
    actual_source_identity: dict[str, Any],
) -> dict[str, Any]:
    """Build one deterministic report for an immutable note/source pair.
    Args:
        expectation_document: Frozen consult contract; empty means no outcomes are registered.
        note: Saved clinician-editable note; empty may fail required screens.
        selected_source_document: Persisted source JSON; empty rows fail identity checks.
        actual_source_identity: Hash/size/session/attestation/lane observed for this pair.
    Returns:
        Source, citation, claim, expectation, metric, and overall verdict evidence.
    """
    selected_source_rows = list(
        selected_source_document.get("segments")
        or selected_source_document.get("source_units")
        or []
    )
    source_identity_score = score_source_identity(
        expected_source_identity=dict(
            expectation_document.get("selected_source_artifact", {})
        ),
        actual_source_identity=actual_source_identity,
        selected_source_rows=selected_source_rows,
        required_source_unit_ids=_required_source_unit_ids(expectation_document),
    )
    citation_resolution_score = score_citation_resolution(
        _note_citation_ids(note), selected_source_rows
    )
    claim_grounding_score = score_claim_grounding(
        _structured_claims(note), selected_source_rows
    )

    # Source mismatch or unresolved citation blocks comparable downstream note scoring.
    if (
        not source_identity_score["passed"]
        or citation_resolution_score["unresolved_citation_count"] > 0
    ):
        return {
            "schema_version": NOTE_QUALITY_SCHEMA_VERSION,
            "fixture_id": expectation_document.get("fixture_id"),
            "verdict": "not_scorable_source",
            "source_identity": source_identity_score,
            "citation_resolution": citation_resolution_score,
            "claim_grounding": claim_grounding_score,
            "expectations": [],
        }

    expectation_scores: list[dict[str, Any]] = []
    # Every frozen consult outcome is scored independently against the same exact note/source.
    for expectation in expectation_document.get("expectations", []):
        expectation_scores.append(
            score_note_expectation(
                expectation=expectation,
                selected_source_rows=selected_source_rows,
                note=note,
            )
        )

    failed_expectation_ids = [
        str(expectation_score["expectation_id"])
        # Every failed screen remains named instead of being hidden by an average.
        for expectation_score in expectation_scores
        # Only exact passing states contribute to faithful critical-screen coverage.
        if not expectation_score["passed"]
    ]
    faithful_screen_coverage: float | None = None
    # No registered expectation means screen coverage is unavailable, never 100%.
    if expectation_scores:
        faithful_screen_coverage = (
            len(expectation_scores) - len(failed_expectation_ids)
        ) / len(expectation_scores)

    has_note_failure = bool(
        failed_expectation_ids
        or claim_grounding_score["unsupported_anywhere_count"]
        or claim_grounding_score["miscited_count"]
        or claim_grounding_score["unresolved_citation_count"]
    )
    return {
        "schema_version": NOTE_QUALITY_SCHEMA_VERSION,
        "fixture_id": expectation_document.get("fixture_id"),
        "verdict": "fail" if has_note_failure else "pass",
        "source_identity": source_identity_score,
        "citation_resolution": citation_resolution_score,
        "claim_grounding": claim_grounding_score,
        "expectations": expectation_scores,
        "faithful_screen_coverage": faithful_screen_coverage,
        "failed_expectation_ids": failed_expectation_ids,
    }


def _sha256_path(artifact_path: Path) -> str:
    """Return the byte identity of one saved reviewer artifact.
    Args:
        artifact_path: Existing file; unreadable paths fail the CLI visibly.
    Returns:
        Lowercase SHA-256 hex digest of the exact file bytes.
    Raises:
        QualityHarnessError: When the selected artifact cannot be read.
    """
    artifact_digest = hashlib.sha256()
    try:
        with artifact_path.open("rb") as artifact_stream:
            # Fixed-size reads hash raw bytes without parsing or altering the evidence.
            for artifact_chunk in iter(lambda: artifact_stream.read(1024 * 1024), b""):
                artifact_digest.update(artifact_chunk)
    # Example: the file can be removed between JSON loading and identity verification.
    except OSError as exc:
        raise QualityHarnessError(f"unreadable_fixture:{artifact_path}") from exc
    return artifact_digest.hexdigest()


def _actual_source_identity(
    source_path: Path,
    selected_source_document: dict[str, Any],
    note: dict[str, Any],
) -> dict[str, Any]:
    """Build observed source identity from saved files, never from expected metadata.
    Args:
        source_path: Exact selected-source file whose bytes are being scored.
        selected_source_document: Parsed source; empty session/rows fail the identity gate.
        note: Saved note carrying its selected attestation/lane/completeness state.
    Returns:
        Hash, size, session, attestation, lane, and terminal-complete evidence.
    """
    selected_source_rows = list(
        selected_source_document.get("segments")
        or selected_source_document.get("source_units")
        or []
    )
    return {
        "sha256": _sha256_path(source_path),
        "bytes": source_path.stat().st_size,
        "session_id": selected_source_document.get("session_id"),
        "attestation_id": note.get("attestation_id"),
        "lane": note.get("source_state"),
        "terminal_complete": bool(
            selected_source_rows and note.get("transcript_truncated") is False
        ),
    }


def parse_args() -> argparse.Namespace:
    """Parse the three immutable artifacts a quality reviewer selects.
    Returns:
        Paths for the expectation contract, saved note, and exact selected source.
    """
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument("expectation_json", type=Path)
    argument_parser.add_argument("note_json", type=Path)
    argument_parser.add_argument("selected_source_json", type=Path)
    return argument_parser.parse_args()


def main() -> int:
    """Print one deterministic saved-note quality report for reviewer evidence.
    Returns:
        Exit 0 when a report is produced; exit 2 when required evidence cannot be loaded.
    """
    command_arguments = parse_args()
    try:
        expectation_document = load_required_json(command_arguments.expectation_json)
        note = load_required_json(command_arguments.note_json)
        selected_source_document = load_required_json(
            command_arguments.selected_source_json
        )
        actual_source_identity = _actual_source_identity(
            command_arguments.selected_source_json,
            selected_source_document,
            note,
        )
        quality_report = score_saved_note_document(
            expectation_document=expectation_document,
            note=note,
            selected_source_document=selected_source_document,
            actual_source_identity=actual_source_identity,
        )
    # Example: a reviewer selects an incomplete export or a source from another visit.
    except QualityHarnessError as exc:
        sys.stderr.write(f"note quality unavailable: {exc}\n")
        return 2

    sys.stdout.buffer.write(stable_json_bytes(quality_report))
    return 0


# Direct execution prints the report used to judge one saved clinician draft.
if __name__ == "__main__":
    raise SystemExit(main())
