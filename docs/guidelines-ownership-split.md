# Guidelines Ownership Split - Ambient Scribe

This document records the migration of domain knowledge and agent instructions to separate files.

## Migration Summary (2026-03-21)

- **Source:** `GEMINI.md` (original)
- **Destination 1:** `GEMINI.md` (Workflow instructions for Gemini CLI)
- **Destination 2:** `docs/domain-reference.md` (Domain knowledge and technical reference)
- **Destination 3:** `docs/architecture.md` (High-level system design)

## Ownership Split

### Gemini CLI (`GEMINI.md`)
- **Owns:** Workflow loop (READ → SCOPE → ACT → VERIFY), autonomy tiers, definition of done, and the router table.
- **Rules:** No domain knowledge here. If you need to know *how* something works, consult the Router.

### Domain Reference (`docs/domain-reference.md`)
- **Owns:** Core technologies, component descriptions, data flow details, environment variables, and quality standards.
- **Rules:** This is a reference only. It does not command the agent's behavior.

### Architecture (`docs/architecture.md`)
- **Owns:** High-level system design, cross-layer interactions, and long-term architectural decisions.

### Shared Guidelines (`.github/instructions/ai-agent-guidelines.instructions.md`)
- **Owns:** Engineering standards and shared practices across both agents (Gemini and Codex).

## Rationale
Separating workflow from domain knowledge reduces context noise and ensures that the agent's behavioral rules remain stable even as the project's technical details evolve.
