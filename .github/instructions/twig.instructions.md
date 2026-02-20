---
applyTo: 'templates/**/*.twig'
---

# Twig / Frontend Conventions - Ambient Scribe

## Structure

- Single template: `templates/scribe/index.html.twig`
- Inline JavaScript (no build step, no bundler)
- CSS via inline `<style>` blocks

## Audio Capture & WebSocket Streaming

The template captures microphone audio via `MediaRecorder` and streams it over a WebSocket to the Python agent layer.

- Use `navigator.mediaDevices.getUserMedia()` to acquire the microphone
- Create a `MediaRecorder` instance and send audio chunks via `WebSocket.send()` as binary data
- The WebSocket URL is constructed from `{{ nemo_websocket_url }}` with the session ID appended
- Handle `MediaRecorder.ondataavailable` to forward each blob to the WebSocket

## Mercure SSE Subscription

Transcription results are delivered back to the browser via Mercure Server-Sent Events.

- Use `{{ mercure_public_url }}` from Twig globals for the Mercure hub URL, not hardcoded URLs
- Subscribe to the session-specific topic via `EventSource`
- Parse incoming SSE events to render transcript segments (speaker role + text) in real time
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
