"""Audit executable medical terms and assemble the combined safety report.

Use these CPU-only rules to bind live rewrites to exact pair-level approval.
The module validates runtime syntax, provenance, review states, and pair parity.
It combines knowledge results without reading files or importing runtime services.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from typing import Any

from clinical_data_audit_knowledge import audit_clinical_knowledge
from clinical_data_audit_shared import (
    ASSET_CONTRACT_VERSION,
    ASSET_ORDER,
    AUDIT_SCHEMA_VERSION,
    LEXICON_SCHEMA_VERSION,
    NORMALIZED_ID_PATTERN,
    REVIEW_SCHEMA_VERSION,
    SHA256_PATTERN,
    RuntimeLexiconPair,
    add_finding,
    contains_complete_phrase,
    has_text,
    normalize_contract_text,
    stable_json_bytes,
    validate_exact_fields,
    validate_human_review,
    validate_privacy,
)

LEDGER_FIELDS = {
    "schema_version",
    "asset_id",
    "asset_version",
    "runtime_lexicon",
    "entries",
}
LEDGER_ENTRY_FIELDS = {
    "id",
    "canonical",
    "variant",
    "status",
    "category",
    "raw_text",
    "expected_visible_text",
    "hard_negatives",
    "evidence_class",
    "locale",
    "intended_consumer",
    "source_artifact",
    "review",
    "privacy_exclusions",
    "safety_rationale",
}
SOURCE_ARTIFACT_FIELDS = {"artifact_id", "sha256", "span", "corpus_class"}


def _parse_runtime_line(
    raw_line: str,
    line_number: int,
    findings: list[dict[str, str]],
) -> tuple[str, list[RuntimeLexiconPair]]:
    """Parse one clinician-visible canonical row into safe rewrite pairs.

    Args:
        raw_line: One executable row; empty columns cannot define a rewrite.
        line_number: Reviewer-facing location; zero would not match the asset.
        findings: Current rows; empty means no malformed field was found yet.

    Returns:
        Normalized canonical plus valid pairs; empty values mean the row failed.
    """
    line_parts = [part.strip() for part in raw_line.split("|")]
    # Missing canonical/variant text cannot define a safe visible rewrite.
    if len(line_parts) < 2 or any(part == "" for part in line_parts):
        add_finding(
            findings,
            "lexicon.invalid_line",
            "medical_lexicon_text",
            f"line-{line_number}",
            "line needs a canonical and non-empty variant",
        )
        return "", []
    canonical = line_parts[0]
    normalized_canonical = normalize_contract_text(canonical)
    runtime_pairs: list[RuntimeLexiconPair] = []
    seen_variants: set[str] = set()
    # Every later column is one exact pair that needs ledger approval.
    for variant in line_parts[1:]:
        normalized_variant = normalize_contract_text(variant)
        # Pass-through or duplicate text is not a valid user-visible rewrite.
        if (
            normalized_variant == normalized_canonical
            or normalized_variant in seen_variants
        ):
            add_finding(
                findings,
                "lexicon.invalid_variant",
                "medical_lexicon_text",
                f"{normalized_canonical}::{normalized_variant}",
                "variant must differ from canonical and be unique",
            )
            continue
        seen_variants.add(normalized_variant)
        runtime_pairs.append(RuntimeLexiconPair(canonical, variant))
    return normalized_canonical, runtime_pairs


def _parse_runtime_lexicon(
    medical_lexicon_text: str, findings: list[dict[str, str]]
) -> list[RuntimeLexiconPair]:
    """Parse executable canonical/variant pairs without runtime imports.

    Args:
        medical_lexicon_text: Exact asset text; empty means no executable pair.
        findings: Current rows; empty means no syntax issue exists yet.

    Returns:
        Ordered valid pairs; malformed rows are excluded and fail visibly.
    """
    # Reviewed text bytes require LF termination when the asset is non-empty.
    if medical_lexicon_text != "" and not medical_lexicon_text.endswith("\n"):
        add_finding(
            findings,
            "lexicon.missing_final_newline",
            "medical_lexicon_text",
            "",
            "runtime lexicon must end with one LF",
        )
    runtime_pairs: list[RuntimeLexiconPair] = []
    normalized_canonicals: list[str] = []
    variant_owners: dict[str, set[str]] = {}
    # Each non-comment line defines independently reviewed executable pairs.
    for line_number, raw_line in enumerate(medical_lexicon_text.splitlines(), start=1):
        stripped_line = raw_line.strip()
        # Comments and blank lines carry no executable activation state.
        if stripped_line == "" or stripped_line.startswith("#"):
            continue
        normalized_canonical, line_pairs = _parse_runtime_line(
            raw_line, line_number, findings
        )
        # A malformed row contributes no executable behavior or ordering identity.
        if normalized_canonical == "":
            continue
        normalized_canonicals.append(normalized_canonical)
        runtime_pairs.extend(line_pairs)
        # Each accepted pair records who owns its heard variant in the UI.
        for runtime_pair in line_pairs:
            normalized_variant = normalize_contract_text(runtime_pair.variant)
            variant_owners.setdefault(normalized_variant, set()).add(
                normalized_canonical
            )
    # Duplicate canonical rows make ownership ambiguous in the user-visible lane.
    if len(normalized_canonicals) != len(set(normalized_canonicals)):
        add_finding(
            findings,
            "lexicon.duplicate_canonical",
            "medical_lexicon_text",
            "",
            "canonical rows must be unique",
        )
    # Canonical order is frozen for stable reviewer evidence.
    if normalized_canonicals != sorted(normalized_canonicals):
        add_finding(
            findings,
            "lexicon.invalid_order",
            "medical_lexicon_text",
            "",
            "canonical rows must sort by normalized text",
        )
    # One variant must never resolve to two different clinician-visible terms.
    for variant, canonical_owners in sorted(variant_owners.items()):
        # Multiple owners create an ambiguous live rewrite.
        if len(canonical_owners) > 1:
            add_finding(
                findings,
                "lexicon.variant_collision",
                "medical_lexicon_text",
                variant,
                "one normalized variant belongs to multiple canonicals",
            )
    return runtime_pairs


def _validate_pair_source(
    source: Any,
    entry_id: str,
    sealed_stems: frozenset[str],
    findings: list[dict[str, str]],
) -> None:
    """Validate artifact-bound evidence for one active rewrite pair.

    Args:
        source: Evidence object; null or empty authorizes no active pair.
        entry_id: Ledger row ID; empty cannot route remediation.
        sealed_stems: Forbidden IDs; empty leaves only corpus-class checks.
        findings: Current rows; empty means no provenance issue exists yet.
    """
    # A non-object cannot bind the rewrite to reviewed evidence.
    if not isinstance(source, dict):
        add_finding(
            findings,
            "review.generic_active_provenance",
            "medical_lexicon_review",
            entry_id,
            "active pair needs an artifact-bound source",
        )
        return
    validate_exact_fields(
        source,
        SOURCE_ARTIFACT_FIELDS,
        "medical_lexicon_review",
        entry_id,
        findings,
    )
    artifact_id = str(source.get("artifact_id", ""))
    corpus_class = str(source.get("corpus_class", ""))
    # Sealed data can never authorize development rewrite behavior.
    if artifact_id in sealed_stems or corpus_class == "sealed_holdout":
        add_finding(
            findings,
            "privacy.sealed_holdout_source",
            "medical_lexicon_review",
            entry_id,
            "active pair cites or derives from a sealed holdout",
        )
        return
    # Only exact development or approved synthetic evidence can activate a pair.
    if corpus_class not in {"development", "approved_synthetic"}:
        add_finding(
            findings,
            "review.generic_active_provenance",
            "medical_lexicon_review",
            entry_id,
            "source corpus must be development or approved_synthetic",
        )
    # Missing ID/span/hash leaves no exact source row for a reviewer.
    if (
        not has_text(source.get("artifact_id"))
        or not has_text(source.get("span"))
        or SHA256_PATTERN.fullmatch(str(source.get("sha256", ""))) is None
    ):
        add_finding(
            findings,
            "review.generic_active_provenance",
            "medical_lexicon_review",
            entry_id,
            "source requires identity, span, and SHA-256",
        )


def _validate_pair_state(
    entry: dict[str, Any],
    entry_id: str,
    findings: list[dict[str, str]],
) -> tuple[str, str]:
    """Validate whether one reviewed rewrite may be active for clinicians.

    Args:
        entry: Parsed review row; empty fails its required fields.
        entry_id: Stable row ID; empty cannot route repair work.
        findings: Current rows; empty means no state issue exists yet.

    Returns:
        Status and category; empty values mean the ledger supplied neither.
    """
    entry_status = str(entry.get("status", ""))
    entry_category = str(entry.get("category", ""))
    # Unknown status cannot decide whether a rewrite is active.
    if entry_status not in {"active", "disabled", "rejected"}:
        add_finding(
            findings,
            "review.invalid_status",
            "medical_lexicon_review",
            entry_id,
            "status must be active, disabled, or rejected",
        )
    # Unknown category could hide semantic inference as ASR correction.
    if entry_category not in {
        "asr_variant",
        "abbreviation_acronym",
        "semantic_synonym",
        "ambiguous_product",
    }:
        add_finding(
            findings,
            "review.invalid_category",
            "medical_lexicon_review",
            entry_id,
            "category is not allowed by the frozen contract",
        )
    human_status = validate_human_review(
        entry.get("review"), "medical_lexicon_review", entry_id, findings
    )
    # Active behavior needs explicit human approval.
    if entry_status == "active" and human_status != "approved":
        add_finding(
            findings,
            "review.unapproved_active_pair",
            "medical_lexicon_review",
            entry_id,
            "active pair requires approved review",
        )
    return entry_status, entry_category


def _validate_active_pair_meaning(
    entry: dict[str, Any],
    entry_id: str,
    entry_status: str,
    entry_category: str,
    findings: list[dict[str, str]],
) -> None:
    """Prevent an active correction from strengthening what the user said.

    Args:
        entry: Reviewed pair; empty cannot match visible source and target text.
        entry_id: Stable row ID; empty cannot route repair work.
        entry_status: Activation state; empty means malformed and stays inactive.
        entry_category: Correction class; empty has no approved semantic meaning.
        findings: Current rows; empty means no meaning issue exists yet.
    """
    # Semantic synonym activation would strengthen user wording.
    if entry_status == "active" and entry_category == "semantic_synonym":
        add_finding(
            findings,
            "review.active_semantic_synonym",
            "medical_lexicon_review",
            entry_id,
            "semantic synonyms cannot be executable live rewrites",
        )
    # Ambiguous product activation would invent specificity.
    if entry_status == "active" and entry_category == "ambiguous_product":
        add_finding(
            findings,
            "review.active_ambiguous_product",
            "medical_lexicon_review",
            entry_id,
            "ambiguous products cannot be executable live rewrites",
        )
    canonical = str(entry.get("canonical", ""))
    variant = str(entry.get("variant", ""))
    # Active rows must show exactly the reviewed variant and canonical.
    if entry_status == "active" and (
        entry.get("raw_text") != variant
        or entry.get("expected_visible_text") != canonical
    ):
        add_finding(
            findings,
            "review.active_text_mismatch",
            "medical_lexicon_review",
            entry_id,
            "active raw/visible text must equal variant/canonical",
        )


def _audit_pair_behavior(
    entry: dict[str, Any],
    entry_id: str,
    findings: list[dict[str, str]],
) -> tuple[tuple[str, str], str]:
    """Validate one pair and return its normalized runtime identity.

    Args:
        entry: Parsed review row; empty fails its required fields.
        entry_id: Stable row ID; empty cannot route repair work.
        findings: Current rows; empty means no earlier pair issue exists.

    Returns:
        Normalized canonical/variant pair and active/disabled/rejected status.
    """
    validate_exact_fields(
        entry, LEDGER_ENTRY_FIELDS, "medical_lexicon_review", entry_id, findings
    )
    entry_status, entry_category = _validate_pair_state(entry, entry_id, findings)
    _validate_active_pair_meaning(
        entry, entry_id, entry_status, entry_category, findings
    )
    normalized_pair = (
        normalize_contract_text(str(entry.get("canonical", ""))),
        normalize_contract_text(str(entry.get("variant", ""))),
    )
    return normalized_pair, entry_status


def _audit_pair_safety(
    entry: dict[str, Any],
    entry_id: str,
    entry_status: str,
    sealed_stems: frozenset[str],
    findings: list[dict[str, str]],
) -> None:
    """Validate evidence, consumer, guards, privacy, and pair rationale.

    Args:
        entry: Parsed pair row; empty has no safety proof.
        entry_id: Stable row ID; empty cannot route remediation.
        entry_status: Activation state; empty means malformed.
        sealed_stems: Forbidden source IDs; empty skips only identity comparison.
        findings: Current rows; empty means no earlier safety issue exists.
    """
    # Generic evidence labels cannot authorize an active visible rewrite.
    if entry_status == "active" and entry.get("evidence_class") not in {
        "observed_development_asr_error",
        "approved_synthetic_asr_fixture",
    }:
        add_finding(
            findings,
            "review.generic_active_provenance",
            "medical_lexicon_review",
            entry_id,
            "active pair needs observed development or approved synthetic evidence",
        )
    # Wrong locale or consumer cannot authorize this live rewrite lane.
    if (
        entry.get("locale") != "en-AU"
        or entry.get("intended_consumer") != "live_post_asr_normalizer"
    ):
        add_finding(
            findings,
            "review.invalid_consumer",
            "medical_lexicon_review",
            entry_id,
            "pair requires en-AU and live_post_asr_normalizer",
        )
    hard_negatives = entry.get("hard_negatives")
    # Missing guards leave no proof that unrelated wording stays unchanged.
    if not isinstance(hard_negatives, list) or not hard_negatives:
        add_finding(
            findings,
            "review.missing_hard_negative",
            "medical_lexicon_review",
            entry_id,
            "each pair needs a hard-negative sentence",
        )
        hard_negatives = []
    # A complete active variant in a guard would be rewritten.
    if entry_status == "active" and any(
        contains_complete_phrase(str(guard), str(entry.get("variant", "")))
        for guard in hard_negatives
    ):
        add_finding(
            findings,
            "review.hard_negative_collision",
            "medical_lexicon_review",
            entry_id,
            "a hard negative contains the complete executable variant",
        )
    # Empty rationale gives no explanation for visible behavior.
    if not has_text(entry.get("safety_rationale")):
        add_finding(
            findings,
            "review.missing_safety_rationale",
            "medical_lexicon_review",
            entry_id,
            "safety rationale must be non-empty",
        )
    _validate_pair_source(
        entry.get("source_artifact"), entry_id, sealed_stems, findings
    )
    validate_privacy(entry, "medical_lexicon_review", entry_id, findings)


def _validate_runtime_binding(
    document: dict[str, Any],
    medical_lexicon_text: str,
    findings: list[dict[str, str]],
) -> None:
    """Require the ledger to bind the exact executable text bytes.

    Args:
        document: Parsed ledger; empty has no runtime binding.
        medical_lexicon_text: Exact text; empty still has a real SHA-256.
        findings: Current rows; empty means no earlier binding issue exists.
    """
    runtime_binding = document.get("runtime_lexicon")
    # An absent object cannot prove which runtime bytes were reviewed.
    if not isinstance(runtime_binding, dict):
        add_finding(
            findings,
            "review.runtime_hash_mismatch",
            "medical_lexicon_review",
            "",
            "runtime_lexicon binding must be an object",
        )
        return
    validate_exact_fields(
        runtime_binding,
        {"schema_version", "sha256"},
        "medical_lexicon_review",
        "runtime_lexicon",
        findings,
    )
    runtime_hash = hashlib.sha256(medical_lexicon_text.encode("utf-8")).hexdigest()
    # Wrong version/hash means the human reviewed different executable data.
    if (
        runtime_binding.get("schema_version") != LEXICON_SCHEMA_VERSION
        or runtime_binding.get("sha256") != runtime_hash
    ):
        add_finding(
            findings,
            "review.runtime_hash_mismatch",
            "medical_lexicon_review",
            "runtime_lexicon",
            "ledger does not bind the exact runtime lexicon",
        )


def _audit_pair_parity(
    runtime_pairs: list[RuntimeLexiconPair],
    pair_counts: Counter[tuple[str, str]],
    active_pairs: set[tuple[str, str]],
    inactive_pairs: set[tuple[str, str]],
    findings: list[dict[str, str]],
) -> None:
    """Compare executable pairs with active, inactive, and duplicate rows.

    Args:
        runtime_pairs: Executable text pairs; empty means no runtime behavior.
        pair_counts: Ledger multiplicity; empty means no review coverage.
        active_pairs: Approved keys; empty authorizes no runtime pair.
        inactive_pairs: Disabled/rejected keys; empty means no candidates.
        findings: Current rows; empty means no earlier parity issue exists.
    """
    # Duplicate pair rows fail even when their stable IDs differ.
    for pair, pair_count in sorted(pair_counts.items()):
        # More than one row makes pair authorization ambiguous.
        if pair_count > 1:
            add_finding(
                findings,
                "review.duplicate_pair",
                "medical_lexicon_review",
                f"{pair[0]}::{pair[1]}",
                "exact pair appears more than once",
            )
    runtime_pair_keys = {
        (normalize_contract_text(pair.canonical), normalize_contract_text(pair.variant))
        # Every executable pair needs its own active ledger row.
        for pair in runtime_pairs
    }
    # Runtime pairs without active review expose unapproved visible behavior.
    for pair in sorted(runtime_pair_keys - active_pairs):
        add_finding(
            findings,
            "review.missing_active_pair",
            "medical_lexicon_review",
            f"{pair[0]}::{pair[1]}",
            "runtime pair has no exact active review row",
        )
    # Active rows absent from runtime overstate shipped behavior.
    for pair in sorted(active_pairs - runtime_pair_keys):
        add_finding(
            findings,
            "review.active_pair_not_runtime",
            "medical_lexicon_review",
            f"{pair[0]}::{pair[1]}",
            "active review pair is absent from runtime",
        )
    # Disabled/rejected rows must never remain executable.
    for pair in sorted((runtime_pair_keys & inactive_pairs) - active_pairs):
        add_finding(
            findings,
            "review.inactive_pair_executable",
            "medical_lexicon_review",
            f"{pair[0]}::{pair[1]}",
            "disabled or rejected pair remains executable",
        )


def _validate_ledger_order(
    entries: list[dict[str, Any]], findings: list[dict[str, str]]
) -> None:
    """Require stable pair ordering for the reviewer-facing audit table.

    Args:
        entries: Valid object rows; empty already has a stable order.
        findings: Current rows; empty means no ordering issue exists yet.
    """
    expected_order = sorted(
        entries,
        key=lambda entry: (
            normalize_contract_text(str(entry.get("canonical", ""))),
            normalize_contract_text(str(entry.get("variant", ""))),
            normalize_contract_text(str(entry.get("status", ""))),
            normalize_contract_text(str(entry.get("id", ""))),
        ),
    )
    # Stable row order keeps repeat audit tables byte-identical for reviewers.
    if entries != expected_order:
        add_finding(
            findings,
            "review.invalid_order",
            "medical_lexicon_review",
            "",
            "entries must sort by canonical, variant, status, and ID",
        )


def _audit_lexicon_review(
    document: dict[str, Any],
    medical_lexicon_text: str,
    runtime_pairs: list[RuntimeLexiconPair],
    sealed_stems: frozenset[str],
    findings: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], set[tuple[str, str]]]:
    """Validate exact pair authorization for the executable lexicon.

    Args:
        document: Parsed ledger; empty authorizes no pair.
        medical_lexicon_text: Exact runtime text for hash binding.
        runtime_pairs: Parsed pairs; empty means no live rewrite.
        sealed_stems: Forbidden source IDs; empty skips only identity comparison.
        findings: Current rows; empty means no earlier ledger issue exists.

    Returns:
        Structured rows and normalized active pair set.
    """
    validate_exact_fields(
        document, LEDGER_FIELDS, "medical_lexicon_review", "", findings
    )
    # Unknown schema rules cannot authorize runtime behavior.
    if document.get("schema_version") != REVIEW_SCHEMA_VERSION:
        add_finding(
            findings,
            "schema.invalid_version",
            "medical_lexicon_review",
            "",
            f"expected {REVIEW_SCHEMA_VERSION}",
        )
    _validate_runtime_binding(document, medical_lexicon_text, findings)
    raw_entries = document.get("entries")
    # A malformed list cannot authorize any rewrite.
    if not isinstance(raw_entries, list):
        add_finding(
            findings,
            "schema.invalid_entries",
            "medical_lexicon_review",
            "",
            "entries must be a list",
        )
        return [], set()
    entries: list[dict[str, Any]] = []
    entry_ids: list[str] = []
    pair_counts: Counter[tuple[str, str]] = Counter()
    active_pairs: set[tuple[str, str]] = set()
    inactive_pairs: set[tuple[str, str]] = set()
    # Every row is validated before aggregate pair parity.
    for raw_entry in raw_entries:
        # Non-object rows cannot describe a reviewed visible rewrite.
        if not isinstance(raw_entry, dict):
            add_finding(
                findings,
                "schema.invalid_entry",
                "medical_lexicon_review",
                "",
                "each review entry must be an object",
            )
            continue
        entries.append(raw_entry)
        entry_id = str(raw_entry.get("id", ""))
        # Malformed or repeated IDs cannot identify an accountable row.
        if NORMALIZED_ID_PATTERN.fullmatch(entry_id) is None or entry_id in entry_ids:
            add_finding(
                findings,
                "review.invalid_or_duplicate_id",
                "medical_lexicon_review",
                entry_id,
                "review IDs must be unique lower-case hyphenated words",
            )
        entry_ids.append(entry_id)
        pair, status = _audit_pair_behavior(raw_entry, entry_id, findings)
        _audit_pair_safety(raw_entry, entry_id, status, sealed_stems, findings)
        pair_counts[pair] += 1
        # Active and inactive sets drive exact pair-level parity.
        if status == "active":
            active_pairs.add(pair)
        else:
            inactive_pairs.add(pair)
    _audit_pair_parity(
        runtime_pairs, pair_counts, active_pairs, inactive_pairs, findings
    )
    _validate_ledger_order(entries, findings)
    return entries, active_pairs


def _audit_context_probe(
    probe: dict[str, Any] | None, findings: list[dict[str, str]]
) -> None:
    """Prove a context-disabled request receives no knowledge content.

    Args:
        probe: Captured seam result; null means prompt visibility was not measured.
        findings: Current rows; empty means no known context leak.
    """
    # No probe records an unmeasured lane without inventing a result.
    if probe is None:
        return
    retrieved_ids = probe.get("retrieved_entry_ids", [])
    rendered_snippets = probe.get("rendered_prompt_snippets", [])
    # Disabled context with any card/snippet would alter the note prompt.
    if probe.get("context_enabled") is False and (retrieved_ids or rendered_snippets):
        leaked_entry_id = str(retrieved_ids[0]) if retrieved_ids else "<prompt>"
        add_finding(
            findings,
            "knowledge.inactive_context_injected",
            "context_probe",
            leaked_entry_id,
            "context-disabled request received knowledge content",
        )


def _audit_development_collisions(
    runtime_pairs: list[RuntimeLexiconPair],
    development_truth_utterances: tuple[str, ...],
    findings: list[dict[str, str]],
) -> None:
    """Find executable variants already spoken in frozen development truth.

    Args:
        runtime_pairs: Exact live rewrites; empty means no collision can occur.
        development_truth_utterances: Manifest speech; empty means unmeasured.
        findings: Current rows; empty means no known corpus collision.
    """
    # Every executable pair is checked only against authorized utterances.
    for runtime_pair in runtime_pairs:
        # Real phrase occurrence would rewrite valid user wording.
        if any(
            contains_complete_phrase(utterance, runtime_pair.variant)
            for utterance in development_truth_utterances
        ):
            pair_id = (
                f"{normalize_contract_text(runtime_pair.canonical)}::"
                f"{normalize_contract_text(runtime_pair.variant)}"
            )
            add_finding(
                findings,
                "lexicon.development_corpus_collision",
                "medical_lexicon_text",
                pair_id,
                "executable variant occurs in official development speech",
            )


def _value_counts(entries: list[dict[str, Any]], field: str) -> dict[str, int]:
    """Count stable row categories without dropping empty legacy values.

    Args:
        entries: Card or pair rows; empty returns an empty object.
        field: Category field; empty counts rows under an empty label.

    Returns:
        Sorted string-to-count mapping; never null.
    """
    return dict(sorted(Counter(str(entry.get(field, "")) for entry in entries).items()))


def audit_clinical_data_documents(
    *,
    clinical_knowledge_document: dict[str, Any],
    medical_lexicon_text: str,
    medical_lexicon_review_document: dict[str, Any],
    context_visibility_probe: dict[str, Any] | None,
    sealed_stems: frozenset[str],
    development_truth_utterances: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Audit three assets and an optional prompt seam into one report.

    Args:
        clinical_knowledge_document: Parsed cards; empty fails closed.
        medical_lexicon_text: Exact runtime text; empty has no executable pair.
        medical_lexicon_review_document: Pair ledger; empty authorizes nothing.
        context_visibility_probe: Seam result; null records no prompt probe.
        sealed_stems: Forbidden source IDs; empty leaves identity unmeasured.
        development_truth_utterances: Manifest speech; empty skips collision metrics.

    Returns:
        Versioned report; findings is empty only when supplied checks pass.
    """
    findings: list[dict[str, str]] = []
    knowledge_entries, eligible_knowledge_count = audit_clinical_knowledge(
        clinical_knowledge_document, sealed_stems, findings
    )
    runtime_pairs = _parse_runtime_lexicon(medical_lexicon_text, findings)
    review_entries, active_review_pairs = _audit_lexicon_review(
        medical_lexicon_review_document,
        medical_lexicon_text,
        runtime_pairs,
        sealed_stems,
        findings,
    )
    _audit_context_probe(context_visibility_probe, findings)
    _audit_development_collisions(runtime_pairs, development_truth_utterances, findings)
    findings.sort(
        key=lambda finding: (
            ASSET_ORDER.get(finding["asset"], 99),
            finding["entry_id"],
            finding["finding_id"],
        )
    )
    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "asset_contract_version": ASSET_CONTRACT_VERSION,
        "status": "pass" if not findings else "fail",
        "summary": {
            "finding_count": len(findings),
            "knowledge_entry_count": len(knowledge_entries),
            "eligible_knowledge_entry_count": eligible_knowledge_count,
            "knowledge_status_counts": _value_counts(knowledge_entries, "status"),
            "runtime_canonical_count": len(
                {normalize_contract_text(pair.canonical) for pair in runtime_pairs}
            ),
            "runtime_noncanonical_pair_count": len(runtime_pairs),
            "review_entry_count": len(review_entries),
            "active_review_pair_count": len(active_review_pairs),
            "review_status_counts": _value_counts(review_entries, "status"),
            "review_category_counts": _value_counts(review_entries, "category"),
            "development_truth_utterance_count": len(development_truth_utterances),
        },
        "findings": findings,
    }


def format_audit_table(audit_report: dict[str, Any]) -> str:
    """Render a stable plain-English finding table for reviewers.

    Args:
        audit_report: Versioned report; empty shows an unknown failing status.

    Returns:
        Newline-terminated table; never empty.
    """
    table_lines = [
        f"status: {audit_report.get('status', 'fail')}",
        "finding_id | asset | entry_id | reviewer meaning",
    ]
    report_findings = audit_report.get("findings", [])
    # A clean audit prints an explicit row instead of an empty table.
    if not report_findings:
        table_lines.append("none | all | all | no contract finding")
    # Findings stay in canonical order for quick UI-impact review.
    for finding in report_findings:
        table_lines.append(
            " | ".join(
                (
                    str(finding.get("finding_id", "")),
                    str(finding.get("asset", "")),
                    str(finding.get("entry_id", "")),
                    str(finding.get("message", "")),
                )
            )
        )
    return "\n".join(table_lines) + "\n"


__all__ = [
    "audit_clinical_data_documents",
    "format_audit_table",
    "stable_json_bytes",
]
