# No Slash Commands Use Playbooks

**Origin:** synthetic-seed
**Agents:** codex

## Bug Description

The agent assumes Codex has Claude-style slash commands and routes the user to `/goat-research` or `/goat-debug` instead of the playbook files.

## Replay Prompt

```
Which Codex slash command should I use for deep research before planning a cross-boundary change in this repo?
```

## Expected Outcome

1. Agent states that Codex does not use slash commands in this repo
2. Agent points to `docs/codex-playbooks/goat-research.md` as the correct resource
3. Agent does NOT invent a slash-command interface

## Failure Mode Tested

- **Codex-specific mechanics**: Playbook files, not slash commands
- **Accuracy**: Agent must not fabricate features that do not exist
