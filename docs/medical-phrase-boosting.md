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

## Extend The Lexicon

Add one row per term:

```text
canonical term|likely ASR variant|another variant
```

Then run:

```bash
strands_agents/.venv/bin/pytest tests/python/test_medical_lexicon.py -q
```

Decode-time transducer phrase boosting is still a human GPU gate for M11. Prove
the exact NeMo 2.7.x API inside the pinned NeMo container before replacing the
post-ASR fallback.
