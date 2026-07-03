# public/js GEMINI

Focus: Browser-side audio capture and real-time transcript rendering.

- **Ask First:** Changes to WebSocket URL, Mercure topic, or PCM down-sampling.
- **Footguns:** #1 (Mercure error banners), #3 (WebM/Opus vs PCM), #4 (Host-only defaults).
- **Check:** `scribe.js:193-255` for down-sampling logic.
- **Rule:** No raw Mercure publishes from PHP; only from Python.
