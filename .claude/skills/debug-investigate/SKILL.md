# Debug Investigation Skill

Diagnosis-first debugging. No fixes until the root cause is understood and human reviews findings.

**If you want to "just try something" before tracing the code path, STOP.**

## Step 1 — Reproduce

Confirm the bug exists. Get the exact error message, stack trace, or unexpected behaviour.

## Step 2 — Trace

Read the actual code path end-to-end. For this project, the typical trace chain is:

```
Twig template (templates/scribe/index.html.twig)
  → JS MediaRecorder / EventSource
    → WebSocket (strands_agents/api/server.py)
      → NemoPipeline (strands_agents/nemo_pipeline.py)
      → NemoSession (strands_agents/nemo_session.py)
      → TranscriptionAgent (strands_agents/agents/transcription_agent.py)
      → RoleMappingState (strands_agents/tools/assign_roles.py)
    → Mercure publish (server.py → Mercure hub)
  → PHP proxy (src/Service/RoleInferenceService.php)
    → ScribeController (src/Controller/ScribeController.php)
```

Read both sides of any boundary the bug crosses (PHP ↔ Python, browser ↔ WebSocket, code ↔ config).

## Step 3 — Diagnose

Write findings with file:line evidence:

```
## Diagnosis

**Symptom:** [what the user sees]
**Root cause:** [what's actually wrong]
**Evidence:**
- [file:line] — [what this code does wrong]
- [file:line] — [why this causes the symptom]
**Blast radius:** [what else could be affected]
**Suggested fix direction:** [approach, not implementation]
```

## Step 4 — Wait

Present diagnosis to human. Do NOT implement fixes until human reviews and approves the direction.

## Constraints

- MUST trace code path before proposing any fix
- MUST include file:line evidence in diagnosis
- MUST NOT apply fixes before human reviews diagnosis
- Check `docs/footguns.md` — the bug may be a known architectural landmine
