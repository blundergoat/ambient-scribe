# CLAUDE.md - v1.13.0 (2026-07-04)

Ambient scribe: medical consultation audio → WebSocket → NeMo GPU → Mercure SSE. Symfony 6.4 (PHP) + FastAPI (Python) + NeMo + Mercure. Core invariant: NeMo owns the single GPU; role inference never runs on it.

Workspace boundary: this checkout is the controlling goat-flow workspace. The selected target project is the project currently being inspected or changed; it may differ from the controlling workspace. Use target-scoped commands such as `git -C <target> status` and keep writes inside the declared target. Target projects do not need goat-flow installed unless the active preset audits goat-flow installation.

## Truth Order
User's explicit instruction for this session > this `CLAUDE.md` file > `.goat-flow/architecture.md`, `.goat-flow/code-map.md`, and `.goat-flow/glossary.md` > loaded goat-* skills and `.goat-flow/skill-docs/` > local instructions in `.github/instructions/` and peer agent files.

## Hard Rules

- Severity order: SECURITY > CORRECTNESS > INTEGRATION > PERFORMANCE > STYLE.
- Questions get Explain mode; directives get action; ambiguous asks get one clarifying question with a recommended default.
- MUST read/search before claims or edits, MUST read every file changed, and cross-boundary work MUST read both sides before acting.
- Preserve cross-file consistency for routes, topics, env vars, hook paths, and skill names; cite file evidence with semantic anchors.
- No features, abstractions, dependencies, or error handling beyond the declared scope.

## Key Resources

- Learning loop, grep before changes: `.goat-flow/learning-loop/footguns/`, `.goat-flow/learning-loop/lessons/`, `.goat-flow/learning-loop/patterns/`, `.goat-flow/learning-loop/decisions/`.
- Tool playbooks: `.goat-flow/skill-docs/playbooks/README.md` is the index; read the relevant playbook before declaring a tool unavailable.
- Project shape: `.goat-flow/architecture.md`, `.goat-flow/code-map.md`, `.goat-flow/glossary.md`, `docs/domain-reference.md`.
- Shared guidance: `.github/instructions/`, `AGENTS.md`, and `GEMINI.md`.

## Execution Loop: READ → SCOPE → ACT → VERIFY

When a goat-* skill is active, the skill's Step 0 satisfies READ/SCOPE - resume at ACT.

**READ** - Gather evidence from real files before any claim. Cross-boundary work MUST read both sides (PHP + Python + Twig/JS). Never fabricate codebase facts. Before declaring any tool or capability unavailable, read the matching playbook in `.goat-flow/skill-docs/playbooks/` (e.g. `browser-use.md`, `page-capture.md`) and run that doc's "Availability Check" section verbatim - project-local CLI tools at `~/.local/bin/` are valid; do not conflate "no harness/MCP tool" with "no tool".

```
BAD:  "WebSocket publishes to topic 'transcribe'" (fabricated)
GOOD: Read server.py:236 → publishes to 'scribe/session/{id}/raw'
```

**SCOPE** - Declare in one step: Intent (question → answer; directive → act), Complexity (Hotfix 2/3, Standard 4/10, System 6/20, Infra 8/25), Mode, files allowed to change, non-goals, blast radius. Re-classify if reads exceed 3× estimate.

**ACT** - Mode transitions MUST be explicit.

| Mode | Behaviour |
|---|---|
| Explain | Walkthrough only; no edits unless asked |
| Plan | Concrete plan, then stop; exit on "LGTM" |
| Implement | Thin vertical slice. 4th substantive read without writing = start coding or report blocker |
| Debug | Diagnosis with file:line first; no fixes until human reviews diagnosis |
| Review | Findings ordered by severity with file:line evidence; no edits unless asked |

State line: `State: [MODE] | Goal: [one line] | Exit: [condition]`. Switch: "Switching to [MODE] because [reason]."

**VERIFY** - Focused checks after each change; broader checks before done.
- Level 1 (note, continue): flaky test, unrelated failure, non-blocking lint warning
- Level 2 (stop, escalate): auth, API contracts, session state, Mercure, NeMo, audio format, cross-boundary
- Re-read every `file:line` cited before presenting findings; unreadable = UNVERIFIED
- After renames, `rg <old-symbol>` across ALL files (including `.md`, `.yaml`, `.json`). Zero refs = pass
- Loop detection: 5+ edits to the same file without green tests → STOP and escalate
**Hallucination red-flags:**

Checks passed without output; Completion without changed files; Fix verification without reproduction; Hedged claims (`should`, `probably`, `looks good`) as verification.

Reject rationalisations listed in `.goat-flow/skill-docs/skill-preamble.md` under "Rationalisations to reject".
- DoD log triggers (conditional, not a separate step):
  - VERIFY caught a failure in your code → `.goat-flow/learning-loop/lessons/` entry
  - Human corrected behaviour → `.goat-flow/learning-loop/lessons/` entry immediately
  - Reusable approach confirmed twice or crosses a boundary → `.goat-flow/learning-loop/patterns/`
  - Architectural trap with file evidence → `.goat-flow/learning-loop/footguns/`

## Autonomy Tiers

**Always:** read/search/diff, run focused tests, run `./scripts/preflight-checks.sh` sub-steps, update docs/tests required by the change.

**Ask First** - touching any of these requires the checklist below:
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
3. Footgun entry checked: [relevant `.goat-flow/learning-loop/footguns/*.md` entry, or "none"]
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

## Artifact Routing

Footguns go to `.goat-flow/learning-loop/footguns/`; lessons go to `.goat-flow/learning-loop/lessons/`; decisions go to `.goat-flow/learning-loop/decisions/`; patterns go to `.goat-flow/learning-loop/patterns/`; local continuity goes to `.goat-flow/logs/sessions/`; active plans go to `.goat-flow/plans/`. Read the target directory `README.md` before editing.

## Working Memory

5+ turn tasks → `.goat-flow/logs/sessions/YYYY-MM-DD-<slug>.md`. Context ladder: summarize → trim into session log → split task if context still grows. Incomplete work MUST update the session log before stopping.

## Hard Constraints

- **GPU exclusivity:** NeMo owns the GPU. Role inference MUST use Bedrock or CPU Ollama - never local GPU.
- **ThreadPoolExecutor:** NeMo inference MUST use `run_in_executor`; never call directly in an async context.
- **Session ID coupling:** UUID flows PHP → Twig → JS → WebSocket → Mercure. All layers MUST match.
- **Audio contract:** Browser streams 16 kHz PCM; `NEMO_STREAM_INPUT_FORMAT` MUST agree. See `.goat-flow/learning-loop/footguns/audio.md`.

## Router Table

| Resource | Read when... |
|---|---|
| `.claude/skills/` | Skill dispatch; see `.claude/skills/goat/SKILL.md` to pick the right one |
| `.goat-flow/learning-loop/footguns/` | Cross-domain landmines with file evidence |
| `.goat-flow/learning-loop/lessons/` | Past agent mistakes |
| `.goat-flow/learning-loop/patterns/` | Reusable successful approaches |
| `.goat-flow/learning-loop/decisions/` | ADRs with rationale (NeMo GPU, Mercure topics) |
| `.goat-flow/architecture.md` | System design, data flows |
| `.goat-flow/code-map.md` | Entry points, file roles |
| `.goat-flow/config.yaml` | Agent/skills/paths config |
| `.goat-flow/skill-docs/` | Shared skill preamble + conventions |
| `.goat-flow/skill-docs/playbooks/` | Tool playbooks (README.md index; read BEFORE declaring a tool unavailable) |
| `docs/architecture.md` | Legacy system doc (retained for now) |
| `docs/domain-php-symfony.md` | PHP/Symfony domain notes |
| `docs/domain-python-nemo.md` | Python/NeMo domain notes |
| `docs/domain-infrastructure.md` | Infra + Terraform notes |
| `docs/nemo-api-notes.md` | NeMo API specifics |
| `AGENTS.md` | Codex workflow (multi-agent) |
| `.github/instructions/` | Per-language coding standards |
| `.goat-flow/plans/` | Current goat-flow plan files and roadmap |

## Essential Commands

```bash
./scripts/preflight-checks.sh    # All quality gates (MUST before done)
./scripts/context-validate.sh    # Workflow-file structural check
composer test                    # PHPUnit
composer analyse                 # PHPStan Level 10
strands_agents/.venv/bin/pytest tests/python/ -q  # Python tests
docker compose up --build        # Full stack (requires NVIDIA GPU)
```
