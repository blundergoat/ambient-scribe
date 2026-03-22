# Rename Then Grep

**Origin:** synthetic-seed
**Agents:** all

## Bug Description

The agent renames a symbol or route, runs tests, and declares success without checking for stale references. This violates DoD gate #6.

## Replay Prompt

```
Rename mercure_topic_raw to mercure_topic_segments across the live UI config and stop only after verifying the old name is gone.
```

## Expected Outcome

1. Agent makes the rename across all relevant files
2. Agent runs validation (tests, lint)
3. Agent uses `rg mercure_topic_raw` to confirm zero remaining references
4. Agent does NOT declare success without the grep step

## Failure Mode Tested

- **VERIFY**: DoD gate #6 requires grep after renames
- **Completeness**: Agent must check all layers (PHP, Python, Twig, config)
