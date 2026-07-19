# Medical Phrase Boosting

Ambient Scribe ships an on-by-default post-ASR medical term normaliser while
decode-time NeMo phrase boosting remains GPU-container pending. Set
`MEDICAL_BOOST_ENABLED=0` when a baseline ASR comparison needs raw output.

## User-Facing Behavior

The fallback only replaces exact word-boundary variants from
`strands_agents/data/medical_lexicon.txt`. It does not fuzzy-match unrelated
words, which keeps the UI from hallucinating boosted terms into normal speech.
Corrections reach the live transcript, the summary, and downloads alike; the
post-visit corrected lane deliberately bypasses the lexicon (ADR-011).

The safety review keeps risky candidates inactive: `heart attack` stays as the
patient's own wording, `thyroid function tests` never collapses to a singular
test, `listen april` is not rewritten to `lisinopril`, and ambiguous
product-class wording such as `steroid cream` is documented as rejected.

## The Three Data Assets

| Asset | Role |
| --- | --- |
| `strands_agents/data/medical_lexicon.txt` | Runtime rows the app loads: `canonical term\|ASR variant\|ASR variant`, sorted by casefolded canonical. |
| `strands_agents/data/medical_lexicon_review.json` | v2 pair ledger (`ambient-scribe-medical-lexicon-review/v2`): one row per canonical/variant pair with category, hard negatives, source artifact, review identity, and safety rationale. Its `runtime_lexicon.sha256` binds the exact `.txt` bytes. |
| `tests/fixtures/scribe/medical-lexicon-synthetic-cases-v1.json` | Synthetic evidence artifact cited by curated (non-observed) pairs; consult-1.2 observed pairs cite the development doctor TextGrid instead. |

The running app reads only the `.txt` file; the ledger and fixture exist for
reviewers and the audit gate.

## Extend The Lexicon

1. Add the row to `medical_lexicon.txt`, keeping rows sorted by casefolded
   canonical. One row per canonical: `canonical term|variant|variant`.
2. Add one active ledger row per new pair to `medical_lexicon_review.json`
   with the frozen v2 fields (`id`, `canonical`, `variant`, `status`,
   `category`, `raw_text`, `expected_visible_text`, `hard_negatives`,
   `evidence_class`, `locale`, `intended_consumer`, `source_artifact`,
   `review`, `privacy_exclusions`, `safety_rationale`). Entries sort by
   canonical, variant, status, then ID.
3. Point `source_artifact` at real evidence: an observed development ASR miss
   (corpus TextGrid) or a case added to the synthetic-cases fixture. Recompute
   the cited artifact's SHA-256 if the fixture changed.
4. Update `runtime_lexicon.sha256` to the SHA-256 of the exact new `.txt`
   bytes.
5. Keep hard negatives honest: a near-collision sentence that must never be
   rewritten, and that does not contain the executable variant itself.

Categories are frozen: `asr_variant`, `abbreviation_acronym`,
`semantic_synonym`, `ambiguous_product`. Semantic synonyms and ambiguous
products can never be active — they exist to document why a candidate stays
inactive.

Then run the CPU-only gates:

```bash
python3 scripts/evaluate-medical-boost.py
strands_agents/.venv/bin/pytest tests/python/test_medical_lexicon.py -q
python3 scripts/clinical-data-audit.py \
  --stem primock57-day1-consultation02-i-have-sore-red-skin \
  --stem primock57-day1-consultation03-i-have-terrible-headache \
  --stem primock57-day1-consultation06-hard-to-breathe \
  --stem primock57-day1-consultation07-i-have-a-cough-and-cold \
  --stem primock57-day1-consultation08-i-have-dry-itchy-skin \
  --stem primock57-day2-consultation03-i-cant-hear-very-well-and-my-face-is-a-bit-numb \
  --stem primock57-day2-consultation09-i-cant-move-my-left-arm \
  --stem primock57-day3-consultation01-lips-swelling-after-eating-a-sandwich \
  --stem primock57-day5-consultation03-im-feeling-very-anxious \
  --stem primock57-day5-consultation09-tired-all-the-time \
  --json-output <unique-path>.json --table-output <unique-path>.txt
```

The evaluator prints the before/after table, checks the runtime SHA-256
binding, and fails when any executable pair lacks an active ledger row or any
guard sentence gets rewritten. The audit additionally validates provenance,
review identity, privacy exclusions, and sweeps every executable variant
against the ten frozen development consultations so a variant can never
rewrite words a real speaker said.

Decode-time transducer phrase boosting is still a human GPU gate for M11. Prove
the exact NeMo 2.7.x API inside the pinned NeMo container before replacing the
post-ASR fallback.
