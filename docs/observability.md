# Observability

Ambient Scribe can emit one JSON object per log line from both Symfony and FastAPI. Use this when you want to inspect one clinician session from page load, through PHP proxy calls, Python role/summary work, and Mercure delivery.

This is a local PoC signal path for synthetic data. PHI/PII redaction is intentionally out of scope here, so logs must avoid transcript text, audio bytes, prompts, SOAP bodies, and raw model responses. Log counts, timings, status, confidence, role labels, and bounded IDs only.

## Enable JSON Logs

Set:

```bash
LOG_FORMAT=json
LOG_LEVEL=INFO
```

`LOG_FORMAT=console` remains the default for readable local development. Optional Strands OTEL export is off by default; set `STRANDS_OTEL=console` or `OTEL_EXPORTER_OTLP_ENDPOINT` only when you want SDK telemetry exported outside JSON logs.

## Canonical Schema

Every structured line uses these fields when relevant:

| Field | Meaning |
| --- | --- |
| `ts` | UTC timestamp |
| `level` | `debug`, `info`, `warning`, or `error` |
| `event` | Stable event slug such as `role_inference.completed` |
| `logger` | Emitting service/logger |
| `session_id` | Browser recording session join key |
| `correlation_id` | PHP-to-Python request join key |
| `duration_ms` | End-to-end operation duration |
| `tokens_in`, `tokens_out`, `tokens_total` | Strands SDK token usage |
| `model_latency_ms` | Strands SDK model latency |
| `cycles` | Strands SDK event-loop cycles |

## Event Taxonomy

- `websocket.chunk_e2e`: live audio chunk timing, segment count, and session ID.
- `role_inference.completed`: role path (`tool`, `freetext`, `heuristic`), fallback flag, confidence, flip state, SDK token/latency metrics, and duration.
- `nemo_session.window_continuity`: one row per emission window with raw vs canonical speaker IDs, overlap-vote evidence, per-ID mapping reasons, remap/merge counts, and emitted spans - never transcript text. Eval runs collect these into `window-continuity.jsonl` via `scripts/window-continuity.py`; requires `LOG_FORMAT=json`.
- `session.quality_tail`: additive JSONL row (`type: "quality_tail"`) written beside a session's quality record when role flips landed after `session.quality` closed (the post-stop settle window). Carries `tail_role_flips_accepted`/`tail_role_flips_suppressed` plus final counters; quiet tails write nothing. `role_inference.completed` log rows carry a `post_finalize` boolean for the same window. Existing `session.quality` fields and cardinality are unchanged; readers that take the first row matching a `session_id` are unaffected because the quality row is always written first.
- **Stop-time drain and stream close policy (M21):** stopping a live recording mirrors the demo-replay drain - the WebSocket closes, the Mercure EventSource stays open until the backend `finalized` event (or a 15s timeout), the tail renders, then the summary fires. The post-visit EventSource then remains open until New Session (`resetSession`) or page unload, same as replay - post-stop role updates therefore still render under the amber-badge policy. The dev panel State tab shows `segmentsReceivedVsStored` (browser `segmentIndex` vs the quality record's `stored_segments`) so a delivery gap is visible without server logs.
- `role_update` Mercure payloads carry an optional `role_stability` object (`level` `stable`/`unstable`, `anchor_remap_rate`, `anchor_remaps`, `phantom_merges`, `windows`, `mapping_changes`, `pending_contrary_mapping`). The browser badge shows green "Roles identified" only when confidence is high AND `level` is `stable`; the dev panel State tab mirrors it as `roleStability`. A missing field means no live audio session existed at publish time (post-disconnect drain) and the browser keeps its last value.
- `summary.completed`: summary section count, duration, and SDK token/latency metrics.
- `mercure.publish.succeeded|retrying|failed|skipped`: Mercure delivery outcome, topic, attempts, duration, and session ID parsed from the topic.
- `strands.client.call`: PHP Strands client proxy call status, path, duration, session ID, and correlation ID.
- `session_history.completed` and `roles_snapshot.completed`: Python-side response log for PHP-proxied history/role calls.
- `eval.role_heuristic`: scenario-level role heuristic accuracy emitted by the eval script.

## SDK Metrics Mapping

Python reads `AgentResult.metrics.get_summary()` after each role and summary agent call:

- `accumulated_usage.inputTokens` -> `tokens_in`
- `accumulated_usage.outputTokens` -> `tokens_out`
- `accumulated_usage.totalTokens` -> `tokens_total`
- `accumulated_metrics.latencyMs` -> `model_latency_ms`
- `total_cycles` -> `cycles`
- `tool_usage.*.execution_stats.success_rate` -> `tool_success_rate`

The PHP client uses one telemetry service as both `RequestMiddleware` and `ResponseObserver`.
`beforeRequest()` injects `X-Correlation-ID`; response-observer hooks cache safe result counts
such as `segments`, `roles`, `stream_events`, and `response_duration_ms`; `afterResponse()`
logs one `strands.client.call` line with status, duration, correlation ID, and those
body-safe counts.

## Reports

Analyze captured logs:

```bash
docker compose logs nemo-agent app | python scripts/analyze-logs.py
python scripts/analyze-logs.py captured.jsonl
```

Run GPU-free role heuristic evaluation:

```bash
python scripts/eval-role-heuristic.py
python scripts/eval-role-heuristic.py | python scripts/analyze-logs.py
```

The analyzer reports latency percentiles, token totals, role path and fallback rate, flip/confidence rates, Mercure outcomes, JSON-parse failures, PHP status mix, and a one-line summary per session.
