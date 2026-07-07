# ADR-006: Skip Post-Visit Correction After Buffer Trim

**Date:** 2026-07-07
**Status:** Accepted

## Context

`AudioBuffer` retains at most `NEMO_BUFFER_MAX_DURATION` (default 900s) of session audio and
silently drops the head beyond that. The post-visit correction endpoint passed
`buffer.full_audio()` (tail-only after a trim) together with the FULL visit's stored rows as
the alignment scaffold. For visits longer than 15 minutes, tail-only second-pass ASR words
were aligned against the whole-visit scaffold; once tail anchors fell below the confidence
gate (`strands_agents/post_visit_correction.py`, search: "confident_anchor_count < max"), the
proportional fallback spread tail words across every row - early clinical history dropped or
misattributed - and `build_summary_context` then PREFERRED those corrected rows as the
summary source. Found as a P1 by the PR #3 Codex review and confirmed live at HEAD.

## Decision

When `buffer.trimmed_seconds > 0`, the correction endpoint does not run second-pass ASR at
all: it returns the existing `_correction_unavailable_response` so the summary uses the live
transcript, which covers the full visit with clinician-visible roles
(`strands_agents/api/server.py`, search: "retention window";
regression: `tests/python/test_post_visit_correction.py`, search:
"falls_back_when_buffer_trimmed").

## Alternatives considered

- **Align only the retained window** and emit corrected rows for the tail: the corrected
  artifact then covers only part of the visit, and summaries preferring it would silently
  lose the early history - the same clinical failure with different mechanics.
- **Stitch live head rows + corrected tail rows into one artifact:** keeps coverage, but
  fabricates provenance (`source_model` on rows the model never re-heard) in a UI that now
  renders per-row provenance popovers, and complicates every consumer of
  `corrected_segments`. Rejected for now; if long-visit correction becomes a priority, this
  is the direction - with an explicit per-row source field and UI support first.

## Consequences

- **Easier:** The corrected artifact is either whole-visit-true or absent; summaries never
  cite partially fabricated alignment. Nothing downstream needs trim awareness.
- **Harder:** Visits over the retention window get no ASR-improved note at all - exactly the
  long consultations where a second pass would help most. Raising
  `NEMO_BUFFER_MAX_DURATION` trades memory for coverage (~1.9 MB per retained minute at
  16 kHz/16-bit) and is the sanctioned knob until head-preserving stitching exists.
