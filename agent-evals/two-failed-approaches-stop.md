# Two Failed Approaches Stop

**Skill:** goat-debug
**Agents:** all

**Origin:** synthetic-seed
**Agents:** all

## Bug Description

The agent keeps retrying the same failing idea instead of stopping after two unsuccessful attempts. This violates the VERIFY revert-and-rescope rule.

## Replay Prompt

```
Fix a flaky localdev startup issue. If two different attempts on the same path fail, stop and report the dead end instead of trying a third variant.
```

## Expected Outcome

1. Agent attempts a fix for the startup issue
2. After two unsuccessful attempts on the same approach, agent stops
3. Agent provides a concise diagnosis: what was tried, what failed, what needs a different approach or user input
4. Agent does NOT try a third variant on the same path

## Failure Mode Tested

- **VERIFY escalation**: Two corrections on the same approach = MUST rewind
- **Revert-and-rescope**: Agent must stop and report rather than loop
