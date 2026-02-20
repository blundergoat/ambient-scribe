---
applyTo: '**'
---

# Commit Message Guidelines

## Purpose
Rules for generating commit messages (used by Copilot's "Generate Commit Message" feature and during code reviews that include commits).

## Format

```
<type>(<scope>): <subject>

<body>
```

### Subject Line
- **Type** (required): `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `style`, `ci`
- **Scope** (optional): the area affected — e.g., `scribe`, `nemo`, `pipeline`, `transcription`, `agent`, `roles`, `mercure`, `docker`, `config`, `terraform`, `scripts`
- **Subject** (required): imperative mood, lowercase, no period, max 72 chars
- If the branch or PR references a GitHub issue, prefix the subject with `#<number> ` (e.g., `#5 add session history endpoint`)

### Body
- Always include a body with 2-5 bullet points explaining **what changed and why**
- Each bullet should be a concrete change, not a vague summary
- Reference specific files, classes, endpoints, or tools when relevant
- Mention cross-layer impacts (e.g., "Python WebSocket format changed, Twig JS updated to match")

## Examples

Good:
```
feat(nemo): add NemoPipeline singleton with GPU diarization support

- Create NemoPipeline class in nemo_pipeline.py wrapping NeMo Parakeet model
- Load model once at import time, share across all WebSocket sessions
- Return Segment objects with speaker label, start/end times, and text
- Add NEMO_MODEL_PROVIDER env var to switch between local and remote inference
```

Good:
```
fix(transcription): wire role assignments through full Mercure pipeline

- Add role field to TranscriptionResult Pydantic model in nemo_pipeline.py
- TranscriptionAgent maps speaker labels to DOCTOR/PATIENT via assign_roles tool
- Mercure SSE events now include role with each transcript segment
- Update Twig template to render role labels alongside transcript text
```

Good:
```
refactor(scripts): parallel agent + Mercure startup in start-dev.sh

- Launch Python agent and Mercure Docker container concurrently
- Replace sequential health checks with single interleaved polling loop
- Add 5-second cleanup timeout with force-kill to prevent hanging on Ctrl+C
- Saves 2-5 seconds per startup
```

Bad (too vague):
```
Update AI agent guidelines and project documentation
```

Bad (no body):
```
feat: add verification to file summariser
```

Bad (generic fluff):
```
Enhance dev script and improve configuration handling
```

## Priorities
- Be specific about what changed — name the files, classes, endpoints, or tools
- Explain the "why" when it isn't obvious from the diff
- Mention cross-layer changes explicitly (PHP + Python + frontend)
- Keep the subject line scannable; put detail in the body

## Guardrails
- Never generate a commit message that is just a single generic sentence
- Never use phrases like "update code", "improve functionality", "various changes", "enhance documentation"
- If changes span multiple features, consider whether they should be separate commits
