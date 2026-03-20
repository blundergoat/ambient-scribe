# No Slash Commands Use Playbooks

**Origin:** synthetic-seed
**Agents:** codex

- Bug description: The agent assumes Codex has Claude-style slash commands and routes the user to `/goat-research` or `/goat-debug` instead of the playbook files.
- Replay prompt: Which Codex slash command should I use for deep research before planning a cross-boundary change in this repo?
- Expected outcome: State that Codex does not use slash commands here, point to `docs/codex-playbooks/goat-research.md`, and avoid inventing a slash-command interface.
- Failure mode tested: Codex-specific mechanics: playbook files, not slash commands.
