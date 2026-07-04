# Clinical Hints

Clinical hints are clinician-in-the-loop suggestions shown beside the completed
consultation. They do not diagnose, prescribe, or change the transcript.

## User-Facing Behavior

When `CLINICAL_HINTS_ENABLED=1`, the summary endpoint may publish structured
hints to `scribe/session/{id}/hints` and return the same hints in the summary
HTTP response. The browser renders them in a dismissible sidebar and keeps the
transcript and SOAP summary usable if no hints exist.

## Current PoC Corpus

`strands_agents/data/clinical_knowledge.json` is a small project-authored PoC
knowledge base. It contains documentation reminders for chest pain, NSAID plus
ACE-inhibitor review, and diabetes medication review. It is not exhaustive
medical guidance and should be replaced or governed before real clinical use.

## Verify

```bash
strands_agents/.venv/bin/pytest tests/python/test_clinical_hints.py tests/python/test_summary.py -q
rg -n "import nemo|import torch" strands_agents/clinical_hints.py strands_agents/medical_lexicon.py
```
