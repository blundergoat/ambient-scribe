---
category: skills
last_reviewed: 2026-07-05
---

# Skill Footguns

## Footgun: Installed skill version drift blocks agent audit even when skills exist

**Status:** active | **Created:** 2026-07-05 | **Evidence:** OBSERVED

**Symptoms:** The agent skills directory exists, but `agent-skills` fails because installed skill frontmatter versions do not match the current goat-flow package.

- **Files:** `.claude/skills/goat/SKILL.md` (search: "goat-flow-skill-version")
- **Files:** `node_modules/@blundergoat/goat-flow/workflow/skills/goat/SKILL.md` (search: "goat-flow-skill-version")
- **What breaks:** The audit version-checks installed skill copies against the current package. A targeted harness repair can be green while the overall Claude audit still fails until the affected agent skills are reinstalled or intentionally version-synced.
- **Evidence:** `goat-flow audit . --agent claude --harness` reported `agent-skills` version mismatch for all seven Claude skills while `feedback-loop-active` stayed `pass`.
- **Prevention:** Keep feedback-loop path repairs separate from skill-template sync. Reinstall skills only when the task explicitly includes installed-skill drift, because it can overwrite local skill edits.
