---
category: setup-tooling
last_reviewed: 2026-07-20
---

# Setup and Installed-Tooling Lessons

## Lesson: Semantic anchors should prefer function names over escaped route strings (2026-07-04)

A generated footgun citation used an escaped decorator string for the WebSocket route in
`strands_agents/api/server.py`. The route existed, but the citation checker did not accept the
escaped quote form.

**Lesson:** For learning-loop citations, prefer stable function-name anchors such as
`(search: "async def transcribe_stream")` over quoted decorator or route literals that require
escaping.

## Lesson: Goat-flow installed skill edits can drift from package templates (2026-07-04)

Updating installed goat-plan skill copies under `.agents/skills/`, `.claude/skills/`, and
`.github/skills/` removed stale project path text, but `goat-flow audit` still compares those
files to the package template in
`node_modules/@blundergoat/goat-flow/workflow/skills/goat-plan/SKILL.md`.

**Lesson:** When changing installed goat-flow skill text for project policy, run
`goat-flow audit` and either accept/report template drift or make the change upstream in the
package before claiming the audit is clean.

## Lesson: Goat-flow setup-green can still hide cross-agent drift (2026-07-05)

**Created:** 2026-07-05
**Evidence:** `.claude/skills/goat/SKILL.md` (search: "goat-flow-skill-version"),
`.github/skills/goat/SKILL.md` (search: "goat-flow-skill-version"), and
`.github/hooks/hooks.json` (search: "\"postToolUse\"").

During a Codex goat-flow 1.13.1 repair, `goat-flow setup . --agent codex` reported
`0 audit checks failed` after Codex config, hooks, and skills were synced. The exact requested
`goat-flow audit . --harness --agent codex` still exited non-zero because the audit drift section
also compared installed `.claude/skills/`, `.github/skills/`, and `.github/hooks/hooks.json`
copies against package templates.

**Lesson:** When the exact audit command is the acceptance gate, trust the audit exit code and
its top-level `drift.status`, not only the setup prompt's numbered checks. If drift remains, sync
every named installed agent copy before declaring the audit clean.

## Lesson: Renamed bundle roots need separate parity checks (2026-07-20)

**Created:** 2026-07-20
**Trigger phase:** VERIFY
**Decision changed:** Compare a deliberately renamed root file separately, then exclude the
installed root from the recursive topical-directory comparison.
**Evidence:** `.goat-flow/skill-docs/playbooks/skill-playbook-authoring-sync.md` (search:
"demonstrates a deliberate renamed bundle root").

During the 1.14.0 refresh, an initial recursive package-parity check reported installed
`README.md` as an extra file after the canonical `skill-quality-testing.md` root had already been
compared to that README. The files were byte-identical; the comparison counted the deliberate
root rename twice.

**Lesson:** For bundles with a documented root-file rename, compare the canonical root to its
installed target first, exclude that target from the remaining recursive directory comparison,
and keep both checks visible in the verification output.
