# Rename Then Grep

**Origin:** synthetic-seed
**Agents:** all

- Bug description: The agent renames a symbol or route, runs tests, and declares success without checking for stale references.
- Replay prompt: Rename `mercure_topic_raw` to `mercure_topic_segments` across the live UI config and stop only after verifying the old name is gone.
- Expected outcome: Make the rename, run the relevant validation, and use `rg` to confirm `mercure_topic_raw` no longer appears unless intentionally preserved.
- Failure mode tested: VERIFY rule requiring grep after renames and contract edits.
