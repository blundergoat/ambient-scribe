# ADR-007: Assessment Section Is Scribe-True (Clinician-Stated Only)

**Date:** 2026-07-07
**Status:** Accepted (user decision, 0.4.0 M00)

## Context

The 2026-07-07 consult-03 manual acceptance run (session `09c43bc6`) produced an Assessment
section that volunteered a diagnosis ("consistent with migraine with aura") and a silent
rule-out ("prior episode of meningitis or other serious pathology was not indicated") that no
clinician said. The same run's Subjective asserted "The onset was sudden rather than gradual"
against a transcript where the patient answered "I don't know really." Nothing in
`MEDICAL_SUMMARY_PROMPT` prevented either: the only anti-invention rule was the
too-short-transcript clause. Whether the note may carry hedged AI pattern recognition is a
scribe-vs-assistant product decision, not a prompt accident to leave implicit.

## Decision

Option B - scribe-true. The Assessment section contains only diagnoses or differentials the
clinician stated in the transcript. AI-inferred diagnoses, suggested conditions, and unstated
rule-outs are barred by prompt rule; when the clinician stated none, the section says no
assessment was documented (mirroring the Objective section's honest behavior). Enforced in
`strands_agents/agents/summary_agent.py` (search: "Only diagnoses or differentials the
clinician stated"), pinned by prompt-rule unit tests in `tests/python/test_summary.py`
(search: "restricts_assessment_to_clinician_statements").

## Alternatives considered

- **Option A - labeled AI suggestion:** keep the hedged AI-inferred assessment but label it
  as AI-suggested pattern recognition in the prompt and the UI section header. Rejected for
  now: it makes the product an assistant in its highest-liability section, needs UI labeling
  work to be honest, and the pilot posture is a scribe clinicians can trust not to add
  clinical content. If assistant-style suggestions become a goal, that is a deliberate
  feature (own UI, own review flow), not a prompt default.
- **Do nothing:** leaves an unlabeled AI diagnosis path in a medical note. Rejected - this is
  the class of error the product cannot make (M00).

## Consequences

- **Easier:** The note never carries clinical content the transcript cannot support; a reader
  can act on the Assessment section as clinician speech. Fidelity regressions are testable
  as prompt-rule presence plus fixture replays instead of "was this hedge appropriate".
- **Harder:** Genuinely useful pattern recognition is withheld; visits where the clinician
  thinks aloud but never names a diagnosis produce "no assessment was documented" notes.
  Revisiting means reopening this ADR with a labeled-suggestion design, not loosening the
  prompt.
