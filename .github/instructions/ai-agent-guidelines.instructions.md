---
applyTo: '**'
---

# AI Agent Guidelines - Ambient Scribe

General principles for AI agents working in this codebase.
Workflow rules (execution loop, DoD, autonomy tiers, stop-the-line) live in CLAUDE.md.

## Core Principles

- Correctness over cleverness. Prefer boring, readable solutions.
- Smallest change that works. Don't refactor adjacent code unless it reduces risk.
- Follow existing patterns before introducing new abstractions or dependencies.
- Full-stack awareness: a change in one layer often affects others (PHP <-> Python <-> Twig <-> Docker).
- Be explicit about uncertainty. If you can't verify something, say so.

## Project-Specific Constraints

- **PHP**: >=8.2, Symfony 6.4, `declare(strict_types=1)`, PSR-12, PHPStan level 10
- **Style**: Single quotes, short arrays, ordered imports, trailing commas in multiline
- **Namespace**: `App\` for src/, `App\Tests\` for tests/
- **Python agent**: FastAPI + Strands SDK + NeMo Parakeet in `strands_agents/`, Python 3.12+
- **Frontend**: Single Twig template (`templates/scribe/index.html.twig`) with inline JS
- **Local dependency**: `blundergoat/strands-client` is a path dependency at `../strands-php-client`

## Engineering Practices

- **Incremental delivery**: Implement -> test -> verify -> then expand. Prefer thin vertical slices over big-bang changes.
- **Bug triage order**: Reproduce -> Localize (which layer) -> Reduce (minimal case) -> Fix root cause -> Add regression test -> Verify end-to-end.

## Testing Conventions

- Framework: PHPUnit 11 (`phpunit.xml.dist`)
- Test files: `tests/Unit/*Test.php` mirroring production paths
- Prefer deterministic unit tests with mocks for HTTP clients, Mercure, event dispatch
- Coverage minimum: 80% (enforced by `composer preflight:coverage`)
- Run focused tests during iteration: `vendor/bin/phpunit tests/Unit/Controller/ScribeControllerTest.php`

## Git Hygiene

- Keep commits atomic — one logical change per commit.
- Don't mix formatting-only changes with behavioral changes.
- Don't rewrite history unless explicitly asked.

## Commit Messages

See `commit-messages.instructions.md` for full format and examples. Key points:
- Use conventional commits: `feat(scope): subject`
- Always include a body with 2-5 bullet points of what specifically changed
- Never write vague one-liners like "Update files" or "Enhance documentation"
