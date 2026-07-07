# Medical Phrase Boosting

Ambient Scribe currently ships an opt-in post-ASR medical term normaliser while
decode-time NeMo phrase boosting remains GPU-container pending.

## User-Facing Behavior

Set `MEDICAL_BOOST_ENABLED=1` when a demo or clinical review needs common drug
and condition names normalised before they appear in the transcript, summary,
or download. Leave it off for baseline ASR comparisons.

The fallback only replaces exact word-boundary variants from
`strands_agents/data/medical_lexicon.txt`. It does not fuzzy-match unrelated
words, which keeps the UI from hallucinating boosted terms into normal speech.

The active M14 review disables three risky prior variants: `heart attack` stays
as the patient's own wording, `thyroid function tests` no longer collapses to a
singular test, and `listen april` is not rewritten to `lisinopril`.

## Extend The Lexicon

Add one row per term:

```text
canonical term|likely ASR variant|another variant
```

Also add or update the matching row in
`strands_agents/data/medical_lexicon_review.json`.

Reviewer checklist:

- evidence that the raw phrase is a real ASR miss;
- expected visible correction;
- false-positive sentence that must stay unchanged;
- category: `asr_variant`, `abbreviation_acronym`, or `semantic_synonym`;
- provenance and reviewer or sign-off note;
- safety rationale for active rows, especially medications or diagnoses.

Then run the CPU-only evaluator and focused tests:

```bash
python3 scripts/evaluate-medical-boost.py
strands_agents/.venv/bin/pytest tests/python/test_medical_lexicon.py -q
```

The evaluator prints the toggle-style table: raw phrase with the fallback off,
visible phrase with the fallback on, and disabled risky phrases that remain raw.
It also fails when an active lexicon row lacks reviewer metadata or when the
review table claims an active correction that is not in the runtime lexicon.

Decode-time transducer phrase boosting is still a human GPU gate for M11. Prove
the exact NeMo 2.7.x API inside the pinned NeMo container before replacing the
post-ASR fallback.
