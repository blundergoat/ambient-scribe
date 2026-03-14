# Agent Evals

Regression tests for CLAUDE.md and skill behaviour. Each file contains a replay prompt from a real incident (or common failure mode). When CLAUDE.md or skills change, replay these prompts and verify the agent handles them correctly.

## How to Use

1. Open a Claude Code session
2. Paste the replay prompt from an eval file
3. Verify the agent's response matches the expected outcome
4. If a previously-passing eval now fails → behavioural regression, revert the CLAUDE.md change

## Files

Each `.md` file contains:
- **Bug description**: what happened
- **Replay prompt**: what to paste into Claude Code
- **Expected outcome**: what the agent should do
- **Failure mode tested**: what CLAUDE.md rule prevents this
