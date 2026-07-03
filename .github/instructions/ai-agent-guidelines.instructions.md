---
applyTo: '**'
---

# AI Agent Guidelines - Ambient Scribe

General principles for AI agents working in this codebase.
Runtime workflow rules (execution loop, autonomy tiers, DoD, router, log files) live in `CLAUDE.md` for Claude Code and `AGENTS.md` for Codex. This file owns shared engineering practice only.

## Core Rules

- Correctness over cleverness. Prefer boring, readable solutions.
- Smallest change that works. Don't refactor adjacent code unless it reduces risk.
- Follow existing patterns before introducing new abstractions or dependencies.
- Full-stack awareness: a change in one layer often affects others (PHP <-> Python <-> Twig <-> Docker).
- Read first, fix second. Trace the actual code path before proposing changes.
- Prove it works: validate with `composer preflight` (tests + PHPStan + CS check).
- Be explicit about uncertainty. If you can't verify something, say so.

## Project-Specific Constraints

- **PHP**: >=8.3, Symfony 6.4, `declare(strict_types=1)`, PSR-12, PHPStan level 10
- **Style**: Single quotes, short arrays, ordered imports, trailing commas in multiline
- **Namespace**: `App\` for src/, `App\Tests\` for tests/
- **Python agent**: FastAPI + Strands SDK + NeMo Parakeet in `strands_agents/`, Python 3.12+
- **Frontend**: Single Twig template (`templates/scribe/index.html.twig`) with inline JS
- **Local dependency**: `blundergoat/strands-client` is a path dependency at `../strands-php-client`

## Architecture

Real-time multi-mode transcription system. Browser captures microphone audio via `PcmStreamer`, encodes it as raw PCM, and streams binary frames over WebSocket to the Python agent layer. NeMo Parakeet performs GPU-accelerated diarization and ASR. The Strands role inference agent assigns mode-specific roles to speaker labels using canonical DOCTOR/PATIENT slots internally. Raw segments, role updates, and summaries are published to the browser via Mercure SSE.

```
Browser (PcmStreamer, raw PCM) → WebSocket → FastAPI (server.py)
                                              ↓
                                        NemoPipeline (GPU diarization + ASR)
                                              ↓
                                        TranscriptionAgent (Strands → Bedrock)
                                              ↓
                                        Mercure SSE → Browser (live transcript)
                                              ↓
ScribeController (Symfony) ← session history ← SessionStore
```

Docker services: `nemo-agent` (GPU), `mercure`, `app` (Symfony).

## Cross-Layer Impact

When changing any layer, check:
1. PHP service wiring (`config/services.yaml`, `config/packages/strands.yaml`)
2. Python agent contracts (Pydantic models in `api/server.py`, WebSocket message formats)
3. Symfony route registration (controller attributes)
4. Twig template (`templates/scribe/index.html.twig`)
5. Docker Compose environment variables (`docker-compose.yml`)
6. PHPUnit tests covering the changed code path
7. PHPStan passes at Level 10

## Git Hygiene

- Keep commits atomic — one logical change per commit.
- Don't mix formatting-only changes with behavioral changes.
- Don't rewrite history unless explicitly asked.

## Testing Conventions

- Framework: PHPUnit 11 (`phpunit.xml.dist`)
- Test files: `tests/Unit/*Test.php` mirroring production paths
- Prefer deterministic unit tests with mocks for HTTP clients, Mercure, event dispatch
- Coverage minimum: 80% (enforced by `composer preflight:coverage`)
- Run focused tests during iteration: `vendor/bin/phpunit tests/Unit/Controller/ScribeControllerTest.php`

## Commit Messages

See `commit-messages.instructions.md` for full format and examples. Key points:
- Use conventional commits: `feat(scope): subject`
- Always include a body with 2-5 bullet points of what specifically changed
- Never write vague one-liners like "Update files" or "Enhance documentation"
