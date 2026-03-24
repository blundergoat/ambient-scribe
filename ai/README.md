# ai/ — Agent Instruction Router

Maps agent instructions to their canonical sources. This directory provides a neutral entry point for any AI coding agent; runtime workflow rules still live in `CLAUDE.md` (Claude Code) and `AGENTS.md` (Codex).

## Instruction Files

| File | Purpose |
|------|---------|
| `instructions/conventions.md` | Stack overview, architecture, commands, hard constraints |
| `instructions/frontend.md` | Frontend patterns: PcmStreamer, StreamOrchestrator, Twig, Mercure SSE |
| `instructions/backend.md` | Backend patterns: Symfony, PHPStan level 10, StrandsClient, testing |
| `instructions/code-review.md` | Code review priorities, approval criteria, anti-patterns |
| `instructions/git-commit.md` | Commit message format and conventions |

## Related Resources

| Resource | Path |
|----------|------|
| Per-language coding standards | `.github/instructions/*.instructions.md` |
| Code review guidelines | `.github/instructions/code-review.instructions.md` |
| Claude Code runtime | `CLAUDE.md` |
| Codex runtime | `AGENTS.md` |
| Skills (Claude) | `.claude/skills/goat-*/` |
| Agent evals | `agent-evals/` |
