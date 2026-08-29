# Ambient Scribe Clinical Intelligence

This file explains the current clinical-intelligence layers that remain after
the clinical hints UI lane was removed:

- **Medical phrase boosting** — post-ASR clinical term normalisation.
- **Clinical RAG hints** — the historical origin of summary grounding.

Both originated in local plan files that are gitignored and may no longer exist;
this README is the durable record.

Last checked: 2026-08-29 against the local repo.

## Short Version

Ambient Scribe has two lightweight clinical assistance layers on top of the
core transcription flow.

Phrase boosting improves the words the clinician sees. It ships an on-by-default post-ASR medical
term normaliser that corrects known clinical terms after NeMo transcription and
before the transcript reaches the UI, summary, or stored session text;
`MEDICAL_BOOST_ENABLED=0` opts back out.

Summary grounding improves what the summary agent can do with the transcript. The remaining
current code keeps a tiny CPU-only clinical knowledge helper for SOAP summary
grounding. The separate rule-based hints lane has been removed.

The important boundary is:

```text
NeMo owns the GPU for speech.
Clinical intelligence stays CPU/Bedrock/Ollama and clinician-in-the-loop.
```

## Why These Belong Together

Both layers improve the same user journey:

```text
spoken consultation
  -> NeMo diarization and ASR
  -> optional medical term correction
  -> role-labelled transcript
  -> grounded SOAP summary
```

Phrase boosting makes clinical words more likely to be displayed correctly. Summary grounding uses the
visible transcript to make the generated summary more useful.
Together they make the app feel less like a generic speech demo and more like a
medical documentation workspace.

## Medical Phrase Normalisation

The original goal was NeMo decode-time phrase boosting for the multitalker
transducer model. That exact GPU-container API is still pending proof. The
post-visit correction lane now carries a decode-phrase hook that stays
inactive by default (`DEFAULT_POST_VISIT_CORRECTION_PHRASE = None` in
`strands_agents/post_visit_correction.py`, with one reviewed phrase approved
for experiments), so decode-time boosting remains unproven in both lanes.

What is shipped now is deliberately narrower: an on-by-default post-ASR
correction fallback.

### What The User Sees

When enabled, common ASR variants can be normalised before they appear in the
transcript. For example, a phrase such as `metro pro lol` can become
`metoprolol` before the clinician reviews the transcript or asks for a summary.

When disabled, the transcript shows raw NeMo ASR text.

### How It Works

Runtime path:

```text
NeMo ASR hypothesis
  -> NemoPipeline._visible_asr_text()
  -> correct_medical_terms()
  -> transcript segments returned to callers
```

Key files:

| File | Responsibility |
| --- | --- |
| `strands_agents/nemo_pipeline.py` | Loads the optional lexicon and applies correction at the pipeline seam. |
| `strands_agents/medical_lexicon.py` | Loads canonical terms and exact ASR variants, then performs safe replacements. |
| `strands_agents/data/medical_lexicon.txt` | Project-curated clinical lexicon (sorted by casefolded canonical); every pair is bound to the review ledger. |
| `strands_agents/data/medical_lexicon_review.json` | v2 pair ledger: per-pair category, hard negatives, source artifact, review identity, and safety rationale, plus the SHA-256 binding of the runtime `.txt`. |
| `scripts/evaluate-medical-boost.py` | CPU-only before/after evaluator; checks the runtime hash binding, per-pair coverage, and guard sentences without loading NeMo or using the GPU. |
| `tests/python/test_medical_lexicon.py` | Proves missing files, exact replacements, and the pipeline seam. |
| `docs/medical-phrase-boosting.md` | Focused operating notes for extending the lexicon. |

Configuration:

```text
MEDICAL_BOOST_ENABLED=1
MEDICAL_LEXICON_PATH=/app/data/medical_lexicon.txt
```

The fallback is on by default; `MEDICAL_BOOST_ENABLED=0` opts a deployment
back out for baseline ASR comparisons. It uses exact word-boundary
replacement only. It does not fuzzy-match random words into clinical terms.
The review ledger keeps risky candidates inactive: `heart attack`,
`thyroid function tests`, and `listen april` stay unchanged, and ambiguous
product wording such as `steroid cream` is documented as rejected.

### Why It Makes The System Better

- Better transcript review: common drug, condition, and investigation names are
  less likely to stay in obviously wrong phonetic forms.
- Better summaries: the summary agent receives cleaner clinical terms.
- Safer scope: exact replacements reduce the risk of inventing clinical terms.
- Easier iteration: the lexicon is a simple text artifact that can be expanded
  and tested without touching the GPU transcription contract.

### Current Limits

- This is not proven NeMo decode-time phrase boosting yet.
- The lexicon is project-curated and audit-gated (`scripts/clinical-data-audit.py`),
  not a licensed clinical vocabulary.
- Before/after clinical ASR accuracy on real GPU replay remains human-pending.
- Turn the feature off for baseline ASR comparisons.

## Clinical Summary Grounding

Assistive layer around the completed consultation: summary grounding. It is
CPU-only and rule-based for the current PoC.

### What The User Sees

After a consult is summarised, the UI can show:

- a SOAP-style summary that had access to short, relevant documentation reminders.

### How Summary Grounding Works

Runtime path:

```text
settled transcript rows (corrected when current, else live)
  -> build_summary_context()
  -> retrieve_clinical_context()  (only when a caller enables context)
  -> summary prompt (api/summary_generation.py)
  -> off-GPU Strands summary agent + fidelity checks (one retry)
  -> JSON summary returned to browser
```

Key files:

| File | Responsibility |
| --- | --- |
| `strands_agents/clinical_context.py` | Validates the governed KB asset fail-closed and retrieves matched snippets. |
| `strands_agents/data/clinical_knowledge.json` | Governed knowledge asset (`ambient-scribe-clinical-knowledge/v1`): reviewed documentation-checklist cards, inactive by default. |
| `docs/clinical-documentation-checklists.md` | Source document each card cites; the asset binds its SHA-256. |
| `strands_agents/api/summary_generation.py` | Adds retrieved snippets to the summary prompt. |
| `strands_agents/agents/summary_agent.py` | Defines the medical SOAP JSON summary agent. |
| `tests/python/test_clinical_context.py` | Proves retrieval, blank transcript behavior, malformed KB fallback, and GPU isolation. |

The current KB carries eleven documentation-checklist cards (chest pain,
NSAID plus ACE inhibitor, diabetes medicines, allergy and antihistamine plans,
asthma review, antibiotic courses, anticoagulants, thyroid monitoring, mental
health safety planning, tiredness workup, and skin infection red flags). It is
not a clinical guideline corpus, and cards reach a summary prompt only when an
internal caller explicitly enables context.

### Why It Makes The System Better

- Better summary quality: relevant context can remind the model to include
  documentation details the transcript implies.
- Better GPU hygiene: retrieval does not compete with NeMo for GPU memory.

### Current Limits

- The KB is synthetic/project-authored and non-exhaustive.
- Real clinical data would need privacy, redaction, governance, and validation
  work before this becomes production clinical decision support.

## Safety Boundaries

These features are documentation assistance only.

They must not:

- make autonomous diagnoses;
- prescribe treatment;
- replace clinician judgement;
- hide or mutate the source transcript in response to retrieved context;
- send retrieval work to the NeMo GPU;
- treat the PoC knowledge base as authoritative clinical guidance.

They should:

- degrade to no correction or no context when inputs are missing;
- preserve transcript and summary review when context retrieval returns no match;
- make pending proof explicit in docs and plans.

## Operating Toggles

| Toggle | Default | Effect |
| --- | --- | --- |
| `MEDICAL_BOOST_ENABLED` | `1` (on) | Post-ASR exact medical term normalisation; set `0` for baseline raw ASR. |
| `MEDICAL_LEXICON_PATH` | `/app/data/medical_lexicon.txt` | Points the pipeline at the lexicon file. |

## Verification

Use these focused checks after changing the lexicon, KB, or summary prompt:

```bash
python3 scripts/evaluate-medical-boost.py
strands_agents/.venv/bin/pytest tests/python/test_medical_lexicon.py -q
strands_agents/.venv/bin/pytest tests/python/test_clinical_context.py tests/python/test_summary.py -q
rg -n "import nemo|import torch" strands_agents/clinical_context.py strands_agents/medical_lexicon.py
```

Use broader checks before shipping cross-boundary changes:

```bash
strands_agents/.venv/bin/pytest tests/python/ -q
composer test
npx playwright test tests/e2e/browser.spec.js
```

GPU proof still needed for true decode-time phrase boosting:

```text
Run a pinned NeMo container spike that proves the exact
EncDecMultiTalkerRNNTBPEModel phrase-boosting API and shows before/after
hypotheses on a clinical audio clip.
```

## Relationship To Other Docs

- `README_STACK.md` lists these features in the full model and runtime
  inventory.
- `docs/medical-phrase-boosting.md` explains how to extend the lexicon.
- The phrase-boosting work and its pending GPU proof originated in a local plan
  file that is gitignored; the pending proof is described above.
- Summary grounding and the removed hints lane likewise originated in a local plan
  file; their durable outcome is described above.
