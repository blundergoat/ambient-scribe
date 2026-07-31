# ADR-003: English-First ASR - Keep Overlap-Aware Multitalker, Defer Multilingual

**Date:** 2026-07-04
**Status:** Accepted
**Author(s):** Matthew Hansen
**Context:** Follows the multitalker/multilingual analysis done during 0.3.0 dependency-upgrade planning.

## Context

The scribe's ASR is NVIDIA's streaming **multitalker Parakeet** (`multitalker-parakeet-streaming-0.6b-v1`, pinned in `docker/nemo/Dockerfile` (search: "multitalker-parakeet-streaming-0.6b-v1")), paired with the streaming **Sortformer** diarizer (`diar_streaming_sortformer_4spk-v2.1`). Its distinguishing capability is *overlap-aware* transcription: per-speaker kernels injected into one model, driven by frame-level Sortformer speaker activity, transcribe fully-overlapped speech.

As of July 2026 that model is **English-only** (built on the `nemotron-speech-streaming-en` backbone). Every multilingual streaming ASR NVIDIA ships - Nemotron-3.5-ASR-Streaming-0.6B (40 languages), parakeet-tdt-0.6b-v3, canary-1b-v2 - is **single-stream** (one speaker at a time). No checkpoint is both overlap-aware *and* multilingual. So multilingual today is reachable only by switching architecture to **diarize-then-ASR** (Sortformer segments → a multilingual single-stream ASR per turn), which **gives up** the concurrent-overlap capability that is the product's differentiator.

Additional constraints at decision time:

- 0.3.0 is a deliberate *focusing* release - collapsing 6 modes to **medical-only**. Adding languages now reverses that focus.
- Solo-developer PoC, no users, synthetic data. No non-English user or market is currently identified - multilingual demand is speculative.
- Sortformer diarization is largely **language-agnostic** (it models speaker activity, not words), so it is *not* the multilingual bottleneck; the ASR model is.

## Decision

- **Ship English-only first.** Keep the overlap-aware multitalker Parakeet + Sortformer pipeline as the product's ASR.
- **Defer multilingual** to a future release, tracked in the release backlog.
- **Bank one hedge now:** keep the ASR model behind a single narrow seam in `strands_agents/nemo_pipeline.py` - one "audio segment → text" boundary (introduce one if the boundary is not already clean) - so a later swap to diarize-then-multilingual-ASR is a *stage swap*, not a pipeline rewrite. Do not let per-speaker-kernel assumptions leak across the pipeline.
- Scope: this decision is about **language coverage only**. It does not change the GPU-singleton/executor model (ADR-001) or the Mercure topic layout (ADR-002).

## Failure Mode Comparison

| Option | What fails | Verdict |
| --- | --- | --- |
| **English-first multitalker (chosen)** | No non-English support until a later milestone | **Accepted** - preserves the overlap differentiator, matches the medical-only focus, zero speculative spend |
| Multilingual now (diarize → single-stream ASR) | Loses concurrent-overlap transcription; adds a new pipeline + language-handling UX + per-language eval; demand unproven | Rejected - trades the core strength for languages nobody has asked for |
| Wait and build nothing | Product stalls with a shippable English product on the shelf | Rejected - the English product is shippable now |

## Reversibility

**Two-way door, if the hedge holds.** With the ASR stage kept swappable, adopting a multilingual model later is a bounded change (swap the model, run ASR per diarized segment, add language selection) rather than a rewrite. Left un-hedged - kernel assumptions spread across the pipeline - it drifts toward a one-way door, which is why the swappable seam is *part of* this decision, not optional.

**Revisit when any of:**

- a real **non-English user or market** is identified - language is a market decision, and this one flips the call;
- NVIDIA ships an **overlap-aware multilingual multitalker** checkpoint - collapses the fork, giving both with no architecture loss (see the "next-gen multitalker checkpoint" watch item in `backlog.md`);
- the English product hits its ceiling and language expansion becomes the next growth lever.

Until then: English-first, ASR stage kept swappable.
