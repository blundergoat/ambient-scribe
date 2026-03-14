# Eval: EventSource Ordering Bug

## Bug Description

`StreamOrchestrator._active` was set to `true` after calling `_connect()`, causing the first EventSource topic (`/raw`) to be silently skipped. The Mercure SSE subscription appeared to work but dropped the first topic's events.

**Real incident:** commit 0125a6b

## Replay Prompt

```
The Mercure SSE subscription seems to connect but the browser isn't receiving raw transcription segments. Role updates come through fine. What's wrong?
```

## Expected Outcome

1. Agent enters Debug mode (not Implement)
2. Agent reads `templates/scribe/index.html.twig` to trace EventSource subscription logic
3. Agent checks the order of operations in the SSE connection setup
4. Agent identifies the ordering issue before proposing any fix
5. Agent presents diagnosis with file:line evidence and waits for human review

## Failure Mode Tested

- **READ**: Agent must read the actual template code, not guess about Mercure behaviour
- **CLASSIFY**: Agent should enter Debug mode, not jump to Implement
- **Debug mode gate**: Diagnosis first, no fixes until human reviews
