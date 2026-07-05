# Ambient Scribe Clinical Intelligence

This file explains the two 0.3.0 clinical-intelligence layers:

- `.goat-flow/plans/0.3.0/M11-medical-phrase-boosting.md`
- `.goat-flow/plans/0.3.0/M12-clinical-rag-hints.md`

Last checked: 2026-07-05 against the local repo.

## Short Version

Ambient Scribe has two lightweight clinical assistance layers on top of the
core transcription flow.

M11 improves the words the clinician sees. It ships an opt-in post-ASR medical
term normaliser that corrects known clinical terms after NeMo transcription and
before the transcript reaches the UI, summary, or stored session text.

M12 improves what the clinician can do with the transcript. It adds a tiny
CPU-only clinical knowledge helper for SOAP summary grounding and a rule-based
hints lane that surfaces non-blocking review suggestions beside the transcript.

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
  -> optional clinical review hints
```

M11 makes clinical words more likely to be displayed correctly. M12 uses the
visible transcript to make the generated summary and review sidebar more useful.
Together they make the app feel less like a generic speech demo and more like a
medical documentation workspace.

## Medical Phrase Normalisation

NeMo decode-time phrase boosting for the multitalker
transducer model. That exact GPU-container API is still pending proof.

What is shipped now is deliberately narrower: an opt-in post-ASR correction
fallback.

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
| `strands_agents/data/medical_lexicon.txt` | Small project-curated clinical lexicon for synthetic demos and local review. |
| `strands_agents/data/medical_lexicon_review.json` | Reviewer table with category, expected correction, false-positive guard, provenance, and rationale. |
| `scripts/evaluate-medical-boost.py` | CPU-only before/after evaluator for lexicon review; does not load NeMo or use the GPU. |
| `tests/python/test_medical_lexicon.py` | Proves missing files, exact replacements, and the pipeline seam. |
| `docs/medical-phrase-boosting.md` | Focused operating notes for extending the lexicon. |

Configuration:

```text
MEDICAL_BOOST_ENABLED=0
MEDICAL_LEXICON_PATH=/app/data/medical_lexicon.txt
```

`MEDICAL_BOOST_ENABLED=1` turns the fallback on. It uses exact word-boundary
replacement only. It does not fuzzy-match random words into clinical terms.
The active review disables risky semantic or false-positive-prone prior rows:
`heart attack`, `thyroid function tests`, and `listen april` stay unchanged.

### Why It Makes The System Better

- Better transcript review: common drug, condition, and investigation names are
  less likely to stay in obviously wrong phonetic forms.
- Better summaries: the summary agent receives cleaner clinical terms.
- Safer scope: exact replacements reduce the risk of inventing clinical terms.
- Easier iteration: the lexicon is a simple text artifact that can be expanded
  and tested without touching the GPU transcription contract.

### Current Limits

- This is not proven NeMo decode-time phrase boosting yet.
- The lexicon is intentionally small and project-curated.
- Before/after clinical ASR accuracy on real GPU replay remains human-pending.
- This feature should stay off for baseline ASR comparisons.

## Clinical RAG And Hints

Assistive layer around the completed consultation: summary
grounding plus structured hints. It is CPU-only and rule-based for the current
PoC.

### What The User Sees

After a consult is summarised, the UI can show:

- a SOAP-style summary that had access to short, relevant documentation reminders;
- a dismissible clinical hints sidebar when the transcript matches a simple review rule;
- no sidebar at all when there are no useful hints.

Hints are suggestions for clinician review. They do not diagnose, prescribe,
edit the transcript, or block the summary.

### How Summary Grounding Works

Runtime path:

```text
visible role-attributed transcript
  -> retrieve_clinical_context()
  -> summary_generation_prompt()
  -> off-GPU Strands summary agent
  -> JSON summary returned to browser
```

Key files:

| File | Responsibility |
| --- | --- |
| `strands_agents/clinical_hints.py` | Loads the tiny KB, retrieves matched snippets, and generates rule hints. |
| `strands_agents/data/clinical_knowledge.json` | Project-authored PoC snippets for documentation reminders. |
| `strands_agents/api/summary_generation.py` | Adds retrieved snippets to the summary prompt. |
| `strands_agents/agents/summary_agent.py` | Defines the medical SOAP JSON summary agent. |
| `tests/python/test_clinical_hints.py` | Proves retrieval, blank transcript behavior, malformed KB fallback, and GPU isolation. |

The current KB covers small PoC reminders for chest-pain documentation, NSAID
plus ACE-inhibitor review, and diabetes medication review. It is not a clinical
guideline corpus.

### How Hints Work

Runtime path:

```text
summary request completes
  -> publish_summary_outputs()
  -> generate_clinical_hints()
  -> Mercure topic scribe/session/{id}/hints
  -> browser hint sidebar
```

The same hints are also returned in the summary HTTP response, so the browser
can still render them if Mercure delivery is unavailable or delayed.

Key files:

| File | Responsibility |
| --- | --- |
| `strands_agents/api/summary_request.py` | Publishes summary events and optional hints. |
| `strands_agents/api/server.py` | Wires `CLINICAL_HINTS_ENABLED` into the summary route. |
| `public/js/scribe-recording.js` | Subscribes the browser to `scribe/session/{id}/hints`. |
| `public/js/scribe-output.js` | Renders and dismisses hint items without blocking transcript or summary review. |
| `templates/scribe/index.html.twig` | Injects `topicHints` and provides the sidebar markup. |
| `tests/python/test_summary.py` | Proves hints are published and returned via HTTP fallback. |

Configuration:

```text
CLINICAL_HINTS_ENABLED=1
```

### Why It Makes The System Better

- Better summary quality: relevant context can remind the model to include
  documentation details the transcript implies.
- Better review workflow: hints call attention to possible note gaps or
  medication-review points while keeping the clinician in control.
- Better resilience: the transcript and summary still work when there are no
  hints or when hint publication fails.
- Better architecture: the hints topic is separate from raw transcript, roles,
  and summary events, so each concern can fail or evolve independently.
- Better GPU hygiene: retrieval and hints do not compete with NeMo for GPU
  memory.

### Current Limits

- The KB is synthetic/project-authored and non-exhaustive.
- Hint rules are simple string/rule checks, not RxNorm, ICD-10, EHR, or
  guideline-grade reasoning.
- Live end-to-end SSE regression for `raw`, `roles`, `summary`, and `hints`
  remains pending in the plan.
- Real clinical data would need privacy, redaction, governance, and validation
  work before this becomes production clinical decision support.

## Safety Boundaries

These features are documentation assistance only.

They must not:

- make autonomous diagnoses;
- prescribe treatment;
- replace clinician judgement;
- hide or mutate the source transcript in response to a hint;
- send RAG or hint work to the NeMo GPU;
- treat the PoC knowledge base as authoritative clinical guidance.

They should:

- keep suggestions short and dismissible;
- degrade to no correction, no context, or no hints when inputs are missing;
- preserve transcript and summary review even when hints fail;
- make pending proof explicit in docs and plans.

## Operating Toggles

| Toggle | Default | Effect |
| --- | --- | --- |
| `MEDICAL_BOOST_ENABLED` | `0` | Enables post-ASR exact medical term normalisation. |
| `MEDICAL_LEXICON_PATH` | `/app/data/medical_lexicon.txt` | Points the pipeline at the lexicon file. |
| `CLINICAL_HINTS_ENABLED` | `1` | Enables summary-time clinical hints and the hints response payload. |

## Verification

Use these focused checks after changing the lexicon, KB, hints, summary prompt,
or browser hint rendering:

```bash
python3 scripts/evaluate-medical-boost.py
strands_agents/.venv/bin/pytest tests/python/test_medical_lexicon.py -q
strands_agents/.venv/bin/pytest tests/python/test_clinical_hints.py tests/python/test_summary.py -q
rg -n "import nemo|import torch" strands_agents/clinical_hints.py strands_agents/medical_lexicon.py
node_modules/.bin/gruff-ts analyse public/js/scribe-output.js public/js/scribe-recording.js templates/scribe/index.html.twig
```

Use broader checks before shipping cross-boundary changes:

```bash
strands_agents/.venv/bin/pytest tests/python/ -q
composer test
npx playwright test tests/e2e/browser.spec.js
./scripts/context-validate.sh
```

GPU proof still needed for true M11 decode-time phrase boosting:

```text
Run a pinned NeMo container spike that proves the exact
EncDecMultiTalkerRNNTBPEModel phrase-boosting API and shows before/after
hypotheses on a clinical audio clip.
```

## Relationship To Other Docs

- `README_STACK.md` lists these features in the full model and runtime
  inventory.
- `docs/medical-phrase-boosting.md` explains how to extend the lexicon.
- `docs/clinical-hints.md` explains the current hints lane and PoC corpus.
- `.goat-flow/plans/0.3.0/M11-medical-phrase-boosting.md` tracks the phrase
  boosting milestone and its pending GPU proof.
- `.goat-flow/plans/0.3.0/M12-clinical-rag-hints.md` tracks the summary
  grounding and hints milestone plus pending live SSE regression.
