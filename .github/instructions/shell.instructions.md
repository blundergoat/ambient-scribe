---
applyTo: 'scripts/**/*.sh'
---

# Shell Script Conventions - Ambient Scribe

## Standards

- Bash (`#!/bin/bash`), not POSIX sh
- `set -euo pipefail` at the top of every script (some use `set -uo pipefail` if non-zero exits are handled explicitly)
- Must pass `shellcheck` with no warnings

## Style

- 4-space indentation
- `UPPER_SNAKE_CASE` for constants and exported variables
- `lower_snake_case` for local variables
- Quote all variable expansions: `"$var"`, `"${var:-default}"`
- Use `[[` over `[` for conditionals
- Use `$(command)` over backticks

## Project Conventions

- `REPO_ROOT` is always `$(cd "$(dirname "$0")/.." && pwd)`
- Colour variables: `RED`, `GREEN`, `YELLOW`, `BLUE`, `CYAN`, `DIM`, `BOLD`, `RESET`
- Status icons: `PASS` (green tick), `FAIL` (red cross), `WARN` (yellow circle), `ARROW` (blue arrow)
- Step output: `printf "  ${ARROW} %-44s" "description"` followed by `pass`, `fail`, or `warn` helper
- Scripts start with a header block comment explaining usage, options, and examples

## Key Scripts

| Script | Purpose |
|--------|---------|
| `start-dev.sh` | Starts NeMo + Python agent + Mercure + PHP app for local dev |
| `deploy.sh` | Builds Docker images, pushes to ECR, redeploys ECS |
| `terraform.sh` | Wrapper around `terraform` CLI with AWS profile setup |
| `preflight-checks.sh` | Runs all quality gates (tests, lint, analysis, coverage) |
| `dependencies-install.sh` | Install from lock files |
| `dependencies-update.sh` | Update to latest within constraints |
| `setup-initial.sh` | First-time Docker-first project setup (GPU, builds, health checks) |
| `start-dev.sh` | Daily lightweight startup (Docker Compose + health checks + log streaming) |
| `health-checks.sh` | Read-only diagnostics (Docker, GPU, containers, services, config) |
| `env-detect.sh` | Shared context sourced by all scripts (paths, dc() wrapper, colors) |

## Error Handling

- Use `|| true` to suppress expected failures (e.g., `docker stop` on a non-running container)
- Use `cleanup()` with `trap cleanup SIGINT SIGTERM` for scripts that start background processes
- Provide clear error messages with suggested fix steps
