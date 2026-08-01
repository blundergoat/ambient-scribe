"""
Tests for the reviewed post-visit correction phrase inventory.

These cover the gate that decides which clinical terms may bias the corrected
lane's decoder. The behaviour that matters most is refusal: an unlisted term must
not reach the decoder by any route, and an ordinary clinician visit must decode
exactly as it did before the inventory existed.
"""

from __future__ import annotations

import importlib.util
import json

import pytest

import correction_phrase_inventory as inv
import post_visit_correction as pvc
from post_visit_correction import (
    PostVisitCorrectionError,
    _reviewed_correction_phrases,
)


def write_inventory(tmp_path, phrases, **extra):
    """Build a minimal inventory file for a focused case."""
    payload = {
        "schema_version": 1,
        "inventory_id": "test-inventory",
        "locale": "en-GB",
        "phrases": [{"phrase": p, "role": "target"} for p in phrases],
        **extra,
    }
    path = tmp_path / "phrases.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    inv._load.cache_clear()
    return path


# ── the shipped inventory ────────────────────────────────────────────────


def test_shipped_inventory_loads_and_declares_targets_and_controls():
    """The packaged list is usable and separates terms under test from regression guards."""
    inv._load.cache_clear()
    targets = inv.phrases_for_role("target")
    controls = inv.phrases_for_role("control")

    assert targets, "an arm with no targets tests nothing"
    assert controls, "without controls a regression on a working term is invisible"
    assert set(targets).isdisjoint(controls)


def test_shipped_inventory_does_not_claim_clinical_review():
    """The records are developer self-review; nothing may imply otherwise."""
    inv._load.cache_clear()
    assert inv.inventory_identity()["clinically_reviewed"] is False


def test_shipped_inventory_carries_hard_negatives_including_a_related_medicine():
    """Boosting is only safe if the terms it must never produce are written down."""
    inv._load.cache_clear()
    negatives = {t.lower() for t in inv.hard_negative_terms()}

    assert "cetirizine" in negatives, "the drug the summariser substituted unprompted"
    assert "pyrimethamine" in negatives, "the wrong-class drug the note drifted toward"


def test_no_hard_negative_is_also_an_approved_phrase():
    """A term cannot be both boosted and forbidden."""
    inv._load.cache_clear()
    assert set(inv.approved_phrases()).isdisjoint(inv.hard_negative_terms())


def test_inventory_identity_digest_covers_the_phrases_not_the_prose():
    """Two runs are comparable only when the accepted phrases match."""
    inv._load.cache_clear()
    baseline = inv.inventory_identity()["phrases_sha256"]

    inv._load.cache_clear()
    assert inv.inventory_identity()["phrases_sha256"] == baseline


def test_identity_digest_changes_when_a_phrase_changes(tmp_path):
    """Editing the list must invalidate comparison against an earlier arm."""
    first = inv.inventory_identity(write_inventory(tmp_path, ["alpha", "beta"]))
    second = inv.inventory_identity(write_inventory(tmp_path, ["alpha", "gamma"]))

    assert first["phrases_sha256"] != second["phrases_sha256"]


def test_digest_ignores_declaration_order(tmp_path):
    """Reordering the file is not a different experiment."""
    a = inv.inventory_identity(write_inventory(tmp_path, ["alpha", "beta"]))
    b = inv.inventory_identity(write_inventory(tmp_path, ["beta", "alpha"]))

    assert a["phrases_sha256"] == b["phrases_sha256"]


# ── the file must fail closed ────────────────────────────────────────────


def test_missing_inventory_raises_rather_than_silently_allowing_everything(tmp_path):
    """An absent gate must not read as an empty gate."""
    with pytest.raises(inv.CorrectionPhraseInventoryError):
        inv.approved_phrases(tmp_path / "absent.json")


def test_malformed_inventory_raises(tmp_path):
    """Half-written JSON cannot be trusted to gate a decoder."""
    path = tmp_path / "phrases.json"
    path.write_text("{not json", encoding="utf-8")
    inv._load.cache_clear()

    with pytest.raises(inv.CorrectionPhraseInventoryError):
        inv.approved_phrases(path)


def test_empty_phrase_list_raises(tmp_path):
    """An inventory that approves nothing is a configuration error, not a silent no-op."""
    with pytest.raises(inv.CorrectionPhraseInventoryError):
        inv.approved_phrases(write_inventory(tmp_path, []))


def test_duplicate_phrase_raises(tmp_path):
    """A repeat would double a term's weight without saying so anywhere."""
    with pytest.raises(inv.CorrectionPhraseInventoryError):
        inv.approved_phrases(write_inventory(tmp_path, ["alpha", "alpha"]))


def test_blank_phrase_raises(tmp_path):
    """An empty string would boost nothing while appearing to boost something."""
    with pytest.raises(inv.CorrectionPhraseInventoryError):
        inv.approved_phrases(write_inventory(tmp_path, ["alpha", "   "]))


# ── the decoder gate ─────────────────────────────────────────────────────


def test_default_off_boosts_nothing():
    """An ordinary clinician visit decodes exactly as it did before the inventory."""
    assert pvc.DEFAULT_POST_VISIT_CORRECTION_PHRASE is None
    assert _reviewed_correction_phrases(None) == []


def test_empty_sequence_boosts_nothing():
    """Asking for no phrases is a valid request, not an error."""
    assert _reviewed_correction_phrases([]) == []


def test_single_reviewed_phrase_is_isolated():
    """One phrase per arm keeps the tested variable to a single term."""
    inv._load.cache_clear()
    target = inv.phrases_for_role("target")[0]

    assert _reviewed_correction_phrases(target) == [target]


def test_whole_inventory_can_run_as_one_arm():
    """The list may be applied together; NeMo's key_phrases_list is plural."""
    inv._load.cache_clear()
    approved = list(inv.approved_phrases())

    assert _reviewed_correction_phrases(approved) == approved


def test_historical_control_phrase_still_accepted():
    """Pre-inventory behaviour stays reproducible."""
    phrase = pvc.APPROVED_POST_VISIT_CORRECTION_PHRASE
    assert _reviewed_correction_phrases(phrase) == [phrase]


def test_unlisted_phrase_is_refused():
    """An unreviewed term must not reach the decoder by any route."""
    with pytest.raises(PostVisitCorrectionError) as raised:
        _reviewed_correction_phrases("amoxicillin 500mg")

    assert raised.value.reason_category == "invalid_phrase_config"


def test_hard_negative_cannot_be_requested_as_a_phrase():
    """The terms boosting must never produce are also not boostable."""
    inv._load.cache_clear()
    for term in inv.hard_negative_terms():
        if term == pvc.APPROVED_POST_VISIT_CORRECTION_PHRASE:
            continue
        with pytest.raises(PostVisitCorrectionError):
            _reviewed_correction_phrases(term)


def test_one_unlisted_phrase_rejects_the_whole_request():
    """A reviewed list must not launder an unreviewed term travelling beside it."""
    inv._load.cache_clear()
    approved = list(inv.approved_phrases())

    with pytest.raises(PostVisitCorrectionError):
        _reviewed_correction_phrases([*approved, "unreviewed term"])


def test_repeated_phrase_is_collapsed():
    """A duplicate request must not weight one term twice."""
    inv._load.cache_clear()
    target = inv.phrases_for_role("target")[0]

    assert _reviewed_correction_phrases([target, target]) == [target]


def test_non_string_phrase_is_refused():
    """A malformed request fails closed rather than reaching OmegaConf."""
    with pytest.raises(PostVisitCorrectionError):
        _reviewed_correction_phrases([None])  # type: ignore[list-item]


def test_unusable_inventory_refuses_every_phrase(monkeypatch, tmp_path):
    """If the gate cannot be read, nothing is boosted - it does not fall open."""
    monkeypatch.setenv(
        "POST_VISIT_CORRECTION_PHRASES_PATH", str(tmp_path / "absent.json")
    )
    inv._load.cache_clear()

    with pytest.raises(PostVisitCorrectionError) as raised:
        _reviewed_correction_phrases("loratadine")

    assert raised.value.reason_category == "invalid_phrase_config"


def test_control_phrase_survives_an_unusable_inventory(monkeypatch, tmp_path):
    """Even the historical control needs a readable gate; it is not a bypass."""
    monkeypatch.setenv(
        "POST_VISIT_CORRECTION_PHRASES_PATH", str(tmp_path / "absent.json")
    )
    inv._load.cache_clear()

    with pytest.raises(PostVisitCorrectionError):
        _reviewed_correction_phrases(pvc.APPROVED_POST_VISIT_CORRECTION_PHRASE)


# ── the decoder config the gate produces ─────────────────────────────────
#
# These need OmegaConf, which ships with NeMo inside the agent container rather
# than in the host virtualenv. The gate tests above are pure Python and always
# run; only the config-shaping assertions depend on it.

omegaconf_missing = importlib.util.find_spec("omegaconf") is None
requires_omegaconf = pytest.mark.skipif(
    omegaconf_missing,
    reason="install omegaconf (ships with NeMo in the agent container) to assert decoder config shaping",
)


class FakeDecodingModel:
    """Minimal stand-in exposing the decoding config surface the hook edits."""

    def __init__(self, strategy="greedy_batch"):
        """Start from the observed shape of the pinned checkpoint."""
        from omegaconf import OmegaConf

        self.cfg = OmegaConf.create(
            {
                "decoding": {
                    "strategy": strategy,
                    "greedy": {
                        "boosting_tree": {"key_phrases_list": None},
                        "boosting_tree_alpha": 0.0,
                        "confidence_method_cfg": {"name": "entropy"},
                    },
                }
            }
        )
        self.applied = None

    def change_decoding_strategy(self, config):
        """Record what the hook asked for instead of touching a real decoder."""
        self.applied = config
        self.cfg.decoding = config


@requires_omegaconf
def test_applying_phrases_sets_the_list_and_leaves_alpha_at_its_default():
    """The phrase list is the only variable an arm changes."""
    inv._load.cache_clear()
    model = FakeDecodingModel()
    phrases = list(inv.phrases_for_role("target"))

    pvc._apply_post_visit_correction_phrase(model, phrases)

    applied = model.applied.greedy
    assert list(applied.boosting_tree["key_phrases_list"]) == phrases
    assert applied.boosting_tree_alpha == pvc._POST_VISIT_CORRECTION_PHRASE_ALPHA == 1.0


@requires_omegaconf
def test_applying_phrases_preserves_the_confidence_contract():
    """Confidence is configured before the phrase and must survive it."""
    inv._load.cache_clear()
    model = FakeDecodingModel()

    pvc._apply_post_visit_correction_phrase(model, inv.phrases_for_role("target")[0])

    assert "confidence_method_cfg" in model.applied.greedy


@requires_omegaconf
def test_no_phrase_leaves_the_decoder_untouched():
    """A default-off visit must not call change_decoding_strategy at all."""
    model = FakeDecodingModel()

    pvc._apply_post_visit_correction_phrase(model, None)

    assert model.applied is None


@requires_omegaconf
def test_non_greedy_batch_decoder_is_refused():
    """Phrase fusion is proven only for the checkpoint's batched greedy decoder."""
    inv._load.cache_clear()
    model = FakeDecodingModel(strategy="beam")

    with pytest.raises(PostVisitCorrectionError) as raised:
        pvc._apply_post_visit_correction_phrase(
            model, inv.phrases_for_role("target")[0]
        )

    assert raised.value.reason_category == "unsupported_phrase_config"
    assert model.applied is None
