# ADR-001: NeMo as GPU Singleton with ThreadPoolExecutor

**Date:** 2026-03-14 (documented; decision made during M1)
**Status:** Accepted

## Context

NeMo multitalker Parakeet models consume significant GPU memory (~4-8GB). Loading multiple instances is not feasible on a 16GB card that also runs the application. GPU inference is synchronous and would block FastAPI's async event loop if called directly, freezing all WebSocket connections.

## Decision

- Load NeMo models once at FastAPI startup as a singleton (`app.state.nemo_pipeline`)
- Run all inference calls through `ThreadPoolExecutor(max_workers=2)` via `run_in_executor`
- The Strands role inference agent uses Bedrock (AWS) or Ollama (CPU-only) — never the GPU

## Consequences

- **Easier:** Single model instance, predictable GPU memory usage, no model loading latency per-request
- **Harder:** No recovery from model crashes (container restart required). Max 2 concurrent transcriptions — others queue. Cannot scale inference horizontally within a single container.
