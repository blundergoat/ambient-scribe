# CLAUDE.md — v1.2.2 (2026-04-22)

Ambient scribe: audio → WebSocket → NeMo GPU → Mercure SSE. Symfony 6.4 (PHP) + FastAPI (Python) + NeMo + Mercure. Supports 6 modes (Medical, Meeting, Interview, TV/Media, Lecture, General). Core invariant: NeMo owns the single GPU; role inference never runs on it.

Truth order: (1) user instruction > (2) this file > (3) skill in use > (4) `.github/instructions/` > (5) system spec.

## Execution Loop: READ → SCOPE → ACT → VERIFY

When a goat-* skill is active, the skill's Step 0 satisfies READ/SCOPE — resume at ACT.

**READ** — Gather evidence from real files before any claim. Cross-boundary work MUST read both sides (PHP + Python + Twig/JS). Never fabricate codebase facts.

```
BAD:  "WebSocket publishes to topic 'transcribe'" (fabricated)
GOOD: Read server.py:236 → publishes to 'scribe/session/{id}/raw'
```

**SCOPE** — Declare in one step: Intent (question → answer; directive → act), Complexity (Hotfix 2/3, Small 3/5, Standard 4/10, System 6/20, Infra 8/25), Mode, files allowed to change, non-goals, blast radius. Re-classify if reads exceed 3× estimate.

**ACT** — Mode transitions MUST be explicit.

| Mode | Behaviour |
|---|---|
| Explain | Walkthrough only; no edits unless asked |
| Plan | Concrete plan, then stop; exit on "LGTM" |
| Implement | Thin vertical slice. 4th substantive read without writing = start coding or report blocker |
| Debug | Diagnosis with file:line first; no fixes until human reviews diagnosis |
| Review | Findings ordered by severity with file:line evidence; no edits unless asked |

State line: `State: [MODE] | Goal: [one line] | Exit: [condition]`. Switch: "Switching to [MODE] because [reason]."

**VERIFY** — Focused checks after each change; broader checks before done.
- Level 1 (note, continue): flaky test, unrelated failure, non-blocking lint warning
- Level 2 (stop, escalate): auth, API contracts, session state, Mercure, NeMo, audio format, cross-boundary
- Re-read every `file:line` cited before presenting findings; unreadable = UNVERIFIED
- After renames, `rg <old-symbol>` across ALL files (including `.md`, `.yaml`, `.json`). Zero refs = pass
- Loop detection: 5+ edits to the same file without green tests → STOP and escalate
- DoD log triggers (conditional, not a separate step):
  - VERIFY caught a failure in your code → `.goat-flow/lessons/` entry
  - Human corrected behaviour → `.goat-flow/lessons/` entry immediately
  - Reusable approach confirmed twice or crosses a boundary → `.goat-flow/patterns.md`
  - Architectural trap with file:line evidence → `.goat-flow/footguns/`

## Autonomy Tiers

**Always:** read/search/diff, run focused tests, run `./scripts/preflight-checks.sh` sub-steps, update docs/tests required by the change.

**Ask First** — touching any of these requires the checklist below:
- Auth: `src/Controller/ScribeController.php`, `config/packages/framework.yaml`
- Session lifecycle: `strands_agents/session_lifecycle.py`, `strands_agents/nemo_session.py`
- PHP ↔ Python contracts: `src/Service/RoleInferenceService.php` ↔ `strands_agents/api/`
- Mercure topics / URLs: `config/packages/mercure.yaml`, `docker-compose.yml`, `.env.example`, `templates/scribe/index.html.twig`
- NeMo pipeline / GPU: `strands_agents/nemo_pipeline.py`, `strands_agents/api/server.py`, `docker/nemo/`
- Audio format contract: `public/js/scribe.js`, `strands_agents/nemo_session.py`, `.env.example`
- Infra: `infra/terraform/`, `docker-compose.yml`, `Dockerfile`
- CI/CD: `.github/workflows/`
- Role-agent provider/model or tool plumbing: `strands_agents/agents/`, `strands_agents/tools/`

Ask First checklist:
1. Boundary touched: [name]
2. Related code read: [yes/no]
3. Footgun entry checked: [relevant `.goat-flow/footguns/*.md` entry, or "none"]
4. Local instruction checked: [local `CLAUDE.md` / `.github/instructions/` file, or "none"]
5. Rollback command: [exact command]

**Never:** overwrite existing files without `ls` the destination first; delete/move/overwrite 5+ files without listing targets and confirming; delete or weaken tests to make a failure disappear; edit `.env` / secrets / credentials; commit or push unless asked; run unscoped `rm -rf`; edit lockfiles/generated outputs; manual chmod 777.

## Definition of Done

1. Lint/typecheck/tests green on changed files (or failure is explicitly explained)
2. `./scripts/context-validate.sh` passes after instruction-file or workflow-file changes
3. No Ask First boundary changed without explicit approval
4. Log entry written when a VERIFY trigger above fired
5. Current state captured in `.goat-flow/logs/sessions/` before stopping incomplete work
6. After any rename or move, `rg <old-name>` across all files (`.md`, `.json`, `.yaml`, config included) returns zero refs

## Working Memory

5+ turn tasks → `.goat-flow/logs/sessions/YYYY-MM-DD-<slug>.md`. Context ladder: summarize → trim into session log → split task if context still grows. Incomplete work MUST update the session log before stopping.

## Hard Constraints

- **GPU exclusivity:** NeMo owns the GPU. Role inference MUST use Bedrock or CPU Ollama — never local GPU.
- **ThreadPoolExecutor:** NeMo inference MUST use `run_in_executor`; never call directly in an async context.
- **Session ID coupling:** UUID flows PHP → Twig → JS → WebSocket → Mercure. All layers MUST match.
- **Audio contract:** Browser streams 16 kHz PCM; `NEMO_STREAM_INPUT_FORMAT` MUST agree. See `.goat-flow/footguns/audio.md`.

## Router Table

| Resource | Read when... |
|---|---|
| `.claude/skills/` | Skill dispatch; see `.claude/skills/goat/SKILL.md` to pick the right one |
| `.goat-flow/footguns/` | Cross-domain landmines with file:line evidence |
| `.goat-flow/lessons/` | Past agent mistakes and patterns |
| `.goat-flow/decisions/` | ADRs with rationale (NeMo GPU, Mercure topics) |
| `.goat-flow/architecture.md` | System design, data flows |
| `.goat-flow/code-map.md` | Entry points, file roles |
| `.goat-flow/config.yaml` | Agent/skills/paths config |
| `.goat-flow/skill-reference/` | Shared skill preamble + conventions |
| `docs/architecture.md` | Legacy system doc (retained for now) |
| `docs/domain-php-symfony.md` | PHP/Symfony domain notes |
| `docs/domain-python-nemo.md` | Python/NeMo domain notes |
| `docs/domain-infrastructure.md` | Infra + Terraform notes |
| `docs/nemo-api-notes.md` | NeMo API specifics |
| `AGENTS.md` | Codex workflow (multi-agent) |
| `GEMINI.md` | Gemini workflow (multi-agent) |
| `.github/instructions/` | Per-language coding standards |
| `milestones/` | Task breakdowns M0–M6 |

## Essential Commands

```bash
./scripts/preflight-checks.sh    # All quality gates (MUST before done)
./scripts/context-validate.sh    # Workflow-file structural check
composer test                    # PHPUnit
composer analyse                 # PHPStan Level 10
strands_agents/.venv/bin/pytest tests/python/ -q  # Python tests
docker compose up --build        # Full stack (requires NVIDIA GPU)
```
