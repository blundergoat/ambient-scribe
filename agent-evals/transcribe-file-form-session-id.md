# Transcribe File Form Session ID

**Origin:** real-incident (commit `35bceb4`)
**Agents:** all

## Bug Description

The FastAPI `/transcribe/file` route accepted `session_id` via query string but ignored multipart form data, so replay uploads silently received a new UUID instead of the caller's session ID.

**Related:** commit `35bceb4`

## Replay Prompt

```
Review the batch transcription route. Replay uploads send session_id as multipart form data and the server is minting a new UUID instead. Restore the contract with the narrowest possible change and say how you'd validate both accepted input paths.
```

## Expected Outcome

1. Agent reads the route in `strands_agents/api/` and its tests
2. Agent accepts `session_id` from both query string and multipart form data
3. Agent preserves the existing query string behaviour
4. Agent mentions targeted API tests for both input paths

## Failure Mode Tested

- **Contract restoration**: Narrowest possible fix without widening scope
- **READ**: Agent must read both the route and its tests before changing
