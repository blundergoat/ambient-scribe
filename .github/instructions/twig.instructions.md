---
applyTo: 'templates/**/*.twig'
---

# Twig / Frontend Conventions - Ambient Scribe

## Structure

- Single template: `templates/scribe/index.html.twig`
- Browser logic lives in `public/js/scribe.js` and optional dev-panel logic in `public/js/scribe-dev.js`
- CSS via inline `<style>` blocks

## Audio Capture & WebSocket Streaming

The template injects session config; `public/js/scribe.js` captures microphone audio through `PcmStreamer` and streams 16 kHz PCM chunks over WebSocket to the Python agent layer.

- Use `navigator.mediaDevices.getUserMedia()` to acquire the microphone
- Use `PcmStreamer` to downsample and emit binary PCM chunks through `WebSocket.send()`
- Use the injected `CONFIG.wsUrl` and `CONFIG.sessionId`; do not reconstruct a parallel WebSocket URL in Twig
- Keep `NEMO_STREAM_INPUT_FORMAT=pcm` aligned with the browser stream unless both sides are changed together

## Mercure SSE Subscription

Transcription results are delivered back to the browser via Mercure Server-Sent Events.

- Use `{{ mercure_public_url }}` from Twig globals for the Mercure hub URL, not hardcoded URLs
- Subscribe to the session-specific `raw`, `roles`, and `summary` topics via `StreamOrchestrator`
- Parse incoming SSE events to render transcript segments, role updates, and summaries in real time
- Handle `EventSource.onerror` — show a user-facing error if the SSE connection drops

## Conventions

- Use Symfony's `{{ path('route_name') }}` for URLs, not hardcoded paths
- Escape user content with `{{ message|e }}` to prevent XSS
- JavaScript uses `const`/`let` (no `var`), arrow functions, template literals

## Error Handling

- Handle `getUserMedia()` rejection (microphone permission denied)
- Handle WebSocket `onerror` and `onclose` — show connection status to the user
- Handle `EventSource.onerror` — display a reconnection or failure message
- Show a clear recording state indicator (recording vs. idle)
