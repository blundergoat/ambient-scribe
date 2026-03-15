# CLAUDE.md

Real-time ambient medical scribe: captures doctor-patient audio via WebSocket, transcribes with NeMo GPU (Sortformer + Parakeet), attributes DOCTOR/PATIENT roles via Strands agent (Bedrock), streams results through Mercure SSE. Stack: Symfony 6.4 (PHP) + FastAPI (Python) + NeMo + Mercure.

## Commands

```bash
composer preflight                     # All quality checks (tests, lint, analysis, coverage)
composer test                          # PHPUnit tests
pytest tests/python/                   # Python tests (from project root)
docker compose up --build              # Full stack (requires NVIDIA GPU)
```

## Hard Rules

- **GPU exclusivity:** NeMo owns the GPU. Role inference uses Bedrock or CPU Ollama — never a local GPU model.
- **ThreadPoolExecutor:** All NeMo inference must use `run_in_executor`. Synchronous GPU work blocks the async event loop.
- **CUDA graph workaround:** After loading Parakeet, disable CUDA graphs (PyTorch 2.8 compat). See `docs/footguns.md` FG-1.
- **Session ID coupling:** UUID flows PHP → Twig → JS → WebSocket URL → Mercure topics. All four layers must match. See `docs/footguns.md` FG-4.
- **Preflight before done:** Always run `composer preflight` before reporting a task complete. Fix failures first.
- **Read first, fix second:** When debugging, read actual code and trace ScribeController → WebSocket → NeMo → Mercure before proposing fixes.
- **Log footguns:** If you discover a new cross-domain pitfall, add it to `docs/footguns.md`.

## Context Router

Read these files on demand — not every session needs every file.

| File | Read when... |
|---|---|
| `docs/code-map.md` | Navigating the repo, finding entry points |
| `docs/architecture.md` | Understanding data flow, Mermaid diagrams, design rationale, endpoint contracts |
| `docs/footguns.md` | Debugging cross-domain issues, CUDA errors, Mercure failures, session coupling |
| `docs/domain-php-symfony.md` | Working in `src/`, `config/`, `templates/` — conventions, commands, quality standards |
| `docs/domain-python-nemo.md` | Working in `strands_agents/`, `tests/python/` — GPU rules, audio pipeline, Mercure publishing |
| `docs/domain-infrastructure.md` | Working in `docker-compose.yml`, `docker/`, `infra/terraform/` — env vars, deployment |
| `docs/nemo-api-notes.md` | NeMo API details, VRAM measurements, model classes, edge case behaviour |
| `docs/troubleshooting.md` | Container compatibility, NeMo version matrix |
| `docs/local-development.md` | Local setup, bare-metal vs Docker, environment config |
| `docs/deployment.md` | AWS deployment scripts, ECR push, ECS redeploy |
| `docs/terraform.md` | Terraform commands, bootstrap, module structure |
| `docs/workflow.md` | Claude Code hooks, skills, quality automation |
| `milestones/` | Task breakdowns for M0–M4 (M0–M1 complete, M2 next) |

## Workflow Rules

- **Plan before building:** Save plans to `docs/PLAN.md` with checkboxes before starting work.
- **Feature checklist:** After implementing a feature, verify: service wiring, endpoint contracts, route registration, template updates, Docker env vars, PHP tests, Python tests, PHPStan Level 10. Full checklist in `docs/domain-php-symfony.md` and `docs/domain-python-nemo.md`.
- **Stop-the-line:** If tests, builds, or analysis break — stop adding features. Fix before continuing.
- **Control scope:** Fix only what's necessary. Log follow-ups as TODOs.
- **Deep first pass:** Reviews and investigations must be thorough. Verify findings by reading surrounding code. "Look deeper" means the first pass was insufficient.
- **Verify external suggestions:** Don't blindly apply Copilot/external review comments. Investigate against the actual codebase first.
- **Full-stack awareness:** Changes may impact PHP, Python, Twig, Docker, and Mercure layers. Consider cross-layer effects.
- **Git hygiene:** One logical change per commit. Atomic and describable.
