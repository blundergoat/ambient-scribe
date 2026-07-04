# Frontend — Ambient Scribe

Vanilla JavaScript served from `public/js/scribe.js` inside a single Twig template (`templates/scribe/index.html.twig`). No bundler, no npm build step. Tailwind CSS loaded from `public/js/tailwind.js`.

## File Layout

| File | Purpose |
|------|---------|
| `public/js/scribe.js` | Shared browser state, role labels, safe DOM helpers, and theme controls |
| `public/js/scribe-streaming.js` | Mercure streams and browser PCM capture |
| `public/js/scribe-recording.js` | Live recording lifecycle and session resets |
| `public/js/scribe-transcript.js` | Transcript card rendering, relabeling, and downloads |
| `public/js/scribe-output.js` | Replay upload, summaries, and clinical hints |
| `public/js/scribe-actions.js` | Post-visit actions, JSON response parsing, summary toggles, and keyboard shortcuts |
| `public/js/scribe-dev.js` | Dev inspector panel |
| `public/js/scribe-fixtures.js` | Dev-only generated WAV fixture replay picker |
| `public/js/tailwind.js` | Tailwind CSS runtime |
| `templates/scribe/index.html.twig` | Single-page template, renders session config from Symfony |

## Key Classes

### PcmStreamer
Captures microphone audio via Web Audio API, downsamples to 16kHz mono, and emits raw PCM chunks. Constructor options: `targetSampleRate`, `chunkMs`, `onChunk`, `onAudioLevel`. Buffers audio data internally and flushes when the byte threshold is reached.

### StreamOrchestrator
Manages EventSource connections to Mercure topics. Handles automatic reconnection with exponential backoff (1s to 30s cap). Tracks `Last-Event-ID` for resuming after brief network interruptions. Subscribe to topics with `subscribe(topic, handler)`, clean up with `disconnectAll()`.

### SessionController
Coordinates the full session lifecycle: microphone acquisition, WebSocket connection to Python, PcmStreamer start/stop, Mercure SSE subscriptions for raw transcripts and role updates.

## JavaScript Conventions

- Use `const` and `let` (never `var`)
- Arrow functions for callbacks
- Template literals for string interpolation
- Classes for stateful components (PcmStreamer, StreamOrchestrator, SessionController)
- Plain functions for stateless helpers (getRoleLabel, getAvatarLabel, createElement)
- `CONFIG` global is set inline by Twig with session ID, WebSocket URL, and Mercure URL

## Twig Conventions

- Use `{{ path('route_name') }}` for URLs, not hardcoded paths
- Escape user content with `{{ variable|e }}` to prevent XSS
- Session config is passed from `ScribeController::index()` as template variables
- JavaScript reads config from a `CONFIG` object set in an inline `<script>` block

## Audio Pipeline

```
getUserMedia() -> PcmStreamer (downsample to 16kHz PCM) -> WebSocket.send(binary)
```

- Request microphone with `navigator.mediaDevices.getUserMedia({ audio: true })`
- PcmStreamer uses `AudioContext.createScriptProcessor(4096, 1, 1)` for capture
- Audio is downsampled from native sample rate to 16kHz and converted to 16-bit PCM
- Chunks are buffered and flushed at the configured interval (default 5000ms)
- WebSocket sends raw binary PCM to `ws://{nemo_websocket_url}/ws/transcribe/{session_id}`

## Mercure SSE

```
EventSource(mercureUrl?topic=scribe/session/{id}/raw)     -> handleRawSegment
EventSource(mercureUrl?topic=scribe/session/{id}/roles)   -> handleRoleUpdate
EventSource(mercureUrl?topic=scribe/session/{id}/summary) -> handleSummaryEvent
EventSource(mercureUrl?topic=scribe/session/{id}/hints)   -> handleClinicalHintsEvent
```

- Separate topics per session keep raw transcript, role, summary, and hint updates independent.
- StreamOrchestrator handles these subscriptions with independent retry state
- Parse `event.data` as JSON; handle parse errors gracefully (log, don't crash)

## Theme and Medical Role Labels

- Dark/light theme persisted in `localStorage` under `ambient-scribe-theme`
- Fixed medical labels render backend DOCTOR/PATIENT roles as Doctor/Patient
- `relabelSegments()` re-renders existing transcript when role inference or a manual override changes labels

## Testing

```bash
npx playwright test                # Run all E2E tests
npx playwright test --headed       # Run with visible browser
```

- Playwright tests in `tests/e2e/` use fake media devices (configured in `playwright.config.js`)
- E2E tests validate the full browser-to-transcript flow
- Contract tests in `tests/e2e/test_contracts.py` verify PHP<->Python API alignment
