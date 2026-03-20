# Transcribe File Form Session ID

**Origin:** real-incident (commit `35bceb4`)
**Agents:** all

- Bug description: The FastAPI `/transcribe/file` route accepted `session_id` via query string but ignored multipart form data, so replay uploads silently received a new UUID.
- Replay prompt: Review the batch transcription route. Replay uploads send `session_id` as multipart form data and the server is minting a new UUID instead. Restore the contract with the narrowest possible change and say how you'd validate both accepted input paths.
- Expected outcome: Read the route and its tests, accept `session_id` from query or form, preserve the existing query behaviour, and mention targeted API tests for both cases.
- Failure mode tested: Contract restoration without widening scope.
