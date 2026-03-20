# Two Failed Approaches Stop

**Origin:** synthetic-seed
**Agents:** all

- Bug description: The agent keeps retrying the same failing idea instead of stopping after two unsuccessful attempts.
- Replay prompt: Fix a flaky localdev startup issue. If two different attempts on the same path fail, stop and report the dead end instead of trying a third variant.
- Expected outcome: After two unsuccessful attempts on the same fix path, stop with a concise diagnosis, what was tried, and what needs a different approach or user input.
- Failure mode tested: VERIFY escalation after two failed approaches.
