# Agent Evals

Regression tests for workflow behaviour. Each file contains a replay prompt from a real incident or common failure mode. When CLAUDE.md, AGENTS.md, or skills change, replay these prompts and verify the agent handles them correctly.

## How to Use

1. Open a Claude Code or Codex session
2. Paste the replay prompt from an eval file
3. Verify the agent's response matches the expected outcome
4. If a previously-passing eval now fails → behavioural regression, revert the change

## Labels

Each eval file has two labels:

- **Origin:** `real-incident` (from git history with commit ref) or `synthetic-seed` (covers a workflow failure mode not yet represented by a real incident)
- **Agents:** `all` (applies to any agent) or `codex` (tests Codex-specific mechanics like playbooks instead of slash commands, or dual-agent scope control)

## Files

Each `.md` file contains:
- **Bug description**: what happened
- **Replay prompt**: what to paste into the agent
- **Expected outcome**: what the agent should do
- **Failure mode tested**: which workflow rule prevents this
