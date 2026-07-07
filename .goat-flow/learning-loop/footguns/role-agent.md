---
category: role-agent
last_reviewed: 2026-07-07
---

# Role-Attribution Agent Footguns

## Footgun: The agent must reach Ollama in-network, and the model must actually be pulled

**Status:** active | **Created:** 2026-07-04 | **Evidence:** ACTUAL_MEASURED

- **Files:** `docker-compose.yml` (search: "OLLAMA_HOST=http://ollama:11434")
- **Files:** `docker-compose.yml` (search: "Ollama only runs under the `ollama` compose profile")
- **Files:** `strands_agents/agents/transcription_agent.py` (search: "OLLAMA_HOST")
- **Files:** `strands_agents/api/role_agent_runtime.py` (search: "_is_provider_unreachable")
- **What breaks:** Role inference AND summaries both call Ollama off-GPU. If `OLLAMA_HOST` points at host.docker.internal:11434, the agent container cannot reach a host-side Ollama on some Docker/WSL2 setups (it resolves to an IPv6 gateway with nothing listening) → every request logs `role_inference.agent_failed_falling_back_to_heuristic` (scrambled DOCTOR/PATIENT at fixed `confidence: 0.4`, so the UI badge is stuck on "Identifying speakers...") and `summary.agent_failed` → 502. The bundled `ollama` Compose service only starts under the `ollama` profile, so local Ollama setups must activate that profile.
- **Two-Ollama trap:** A host Ollama (`localhost:11434`) and the Compose `ollama` service are easy to confuse; the agent ONLY uses the container one. `start-dev.sh` historically validated and pulled into the host one, so a fresh `ollama_data` volume could lack the model while the script printed "Ready!" (PR #3 bot review confirmed this); it now ensures the model inside the compose service after startup (`scripts/start-dev.sh`, search: "Model in compose ollama" / "available in compose ollama"). Manual pull target is still the CONTAINER: `docker compose exec ollama ollama pull qwen3.5:9b` (persists in the `ollama_data` volume). A `summary.agent_failed`/`role_inference.agent_failed` with `duration_ms` in the low tens means the model is missing or unreachable, NOT slow - a real CPU generation is tens of seconds. Residual: `scripts/health-check-localdev.sh` (search: "detect_running_ollama_host") still probes host endpoints and can report a healthy Ollama the agent never talks to.
- **Exact-tag trap:** Ollama presence checks must compare the full `NAME:TAG` from `ollama list`; the agents request the exact configured `ROLE_AGENT_OLLAMA_MODEL`, so substring/prefix matches let `qwen3.5:7b` satisfy a `qwen3.5:9b` config and the consultation fails after preflight passed. Both checks now match exactly, treating a bare name as `:latest` (`strands_agents/api/server.py`, search: "is_pulled = model in names"; `scripts/check-ai-model.sh`, search: "expected_tag"). Regressions: `tests/python/test_api.py` (search: "prefix_tag_is_not_treated_as_pulled").
- **`.env` override trap:** A stale `.env` `OLLAMA_HOST=http://host.docker.internal:11434` silently overrides the compose default on every recreate, so the agent keeps pointing at the unreachable host address even after the default is fixed. `OLLAMA_HOST` is therefore PINNED to a literal `http://ollama:11434` in `docker-compose.yml` (not `${OLLAMA_HOST:-...}`), so `.env` can no longer reintroduce it. Diagnose with `docker compose exec nemo-agent sh -c 'echo $OLLAMA_HOST'`.
- **Fix:** Pin `OLLAMA_HOST=http://ollama:11434` (literal), keep `nemo-agent depends_on ollama` optional for Bedrock setups, activate the `ollama` Compose profile for local Ollama setups, and publish no host port on it (avoids clashing with a host Ollama on 11434). The browser also pre-flights `GET /agent/model-health` and refuses to start a consultation when the model is unreachable - and that preflight now fails closed for Bedrock (credential/region resolution) and unknown provider values instead of assuming non-Ollama providers are configured (`strands_agents/api/server.py`, search: "unknown ROLE_AGENT_MODEL_PROVIDER").

## Footgun: The Ollama role model must support tool calling
**Status:** active | **Created:** 2026-03-21 | **Evidence:** ACTUAL_MEASURED

- **Files:** `.env.example` (search: "ROLE_AGENT_OLLAMA_MODEL=qwen3.5:9b")
- **Files:** `docker-compose.yml` (search: "ROLE_AGENT_MODEL_PROVIDER=${ROLE_AGENT_MODEL_PROVIDER:-ollama}")
- **Files:** `strands_agents/agents/transcription_agent.py` (search: "You MUST call the assign_roles tool")
- **Files:** `strands_agents/agents/transcription_agent.py` (search: "from tools.assign_roles import assign_roles")
- **Files:** `strands_agents/agents/transcription_agent.py` (search: "tools=[assign_roles]")
- **Files:** `strands_agents/tools/assign_roles.py` (search: "def assign_roles")
- **What breaks:** `assign_roles` is wired as a Strands tool. Models without tool/function calling support fall back to free-text JSON behaviour, which is slower and less reliable.
- **Evidence:** The agent prompt explicitly says it MUST call `assign_roles`, the agent constructor passes `tools=[assign_roles]`, and the default Ollama model is pinned in both `.env.example` and `docker-compose.yml`.

## Footgun: Role agents must not echo transcript segments through tools
**Status:** active | **Created:** 2026-07-05 | **Evidence:** ACTUAL_MEASURED

- **Files:** `strands_agents/tools/assign_roles.py` (search: "def store_pending_role_segments")
- **Files:** `strands_agents/api/role_agent_runtime.py` (search: "store_pending_role_segments(session_id, segments)")
- **Files:** `strands_agents/agents/transcription_agent.py` (search: "Call assign_roles on every request, including weak or early evidence.")
- **What breaks:** If the model receives every transcript row and must echo those rows back through `assign_roles`, output grows with the visit and Strands can replace the tool call with a `max_tokens truncation` error. If the prompt permits "wait for stronger evidence", the role agent can skip the tool on early ambiguous turns, leaving the browser on raw speaker labels until a fallback runs.
- **Evidence:** M19 replay started from a baseline of 9 `max_tokens truncation` events in 30 minutes. Moving transcript rows into server-side pending state, bounding role evidence, raising the role token budget, and requiring `assign_roles` on every request produced `scripts/eval-fixtures.sh --all` with zero `max_tokens truncation`, zero `role_inference.tool_not_invoked`, and zero `websocket.error` logs for the final run window.
- **Prevention:** Keep transcript segments server-side, send only bounded evidence to the model, keep tool returns compact, and treat any role-agent prompt change as incomplete until a full eval replay plus log grep proves no truncation or tool-miss events.

## Footgun: session.quality role counters are a disconnect-time snapshot
**Status:** active | **Created:** 2026-07-05 | **Evidence:** ACTUAL_MEASURED

- **Files:** `strands_agents/api/streaming_session.py` (search: "await services.enqueue_role_inference(session_id, tail_payloads)")
- **Files:** `strands_agents/session_quality.py` (search: "role_flips_suppressed")
- **Files:** `scripts/role-timeline.py` (search: "def quality_check_row")
- **What breaks:** `_finalize_after_disconnect` enqueues the finalize tail as one more role batch and then immediately builds the quality record, so the role worker is often still draining when `role_flips_accepted`/`role_flips_suppressed` are snapshotted. Any flip decision made after `finalized_at` exists only in the server logs; diagnostics that trust `quality.json` under-count and can send an investigation down the wrong layer.
- **Evidence:** consultation-08 in `var/quality/runs/20260705T022647Z` recorded `role_flips_suppressed: 1` at `finalized_at 02:31:28.7Z` while the log-derived timeline shows a second `role_mapping.flip_suppressed` at `02:31:34.3Z` plus a final accepted update at `02:31:35.3Z`.
- **Prevention:** treat `role-timeline.jsonl` as the complete flip record; `scripts/role-timeline.py --quality-json` appends a `role_timeline.quality_check` row and `scripts/eval-fixtures.sh` warns when the counters disagree. Do not "fix" a counter mismatch by editing the counters - either reconcile against the timeline or move the snapshot after the role queue drains (a session-lifecycle change requiring Ask First).

## Footgun: Suppressed role flips are still successful tool decisions
**Status:** active | **Created:** 2026-07-05 | **Evidence:** ACTUAL_MEASURED

- **Files:** `strands_agents/tools/assign_roles.py` (search: "last_flip_suppressed")
- **Files:** `strands_agents/api/role_agent_runtime.py` (search: "suppressed_flip_added")
- **What breaks:** Flip damping intentionally keeps the current DOCTOR/PATIENT mapping and does not append a new mapping-history row. If runtime code treats "history did not grow" as "the tool was not invoked", it falls through to the keyword fallback, which can apply the same flip the tool just suppressed and relabel the user's whole transcript anyway.
- **Evidence:** During M16, consultation-03 and consultation-08 logs showed `role_mapping.flip_suppressed`, then `role_inference.tool_not_invoked`, then fallback `role_mapping.flip_detected`. The regression test `tests/python/test_role_inference.py` (search: "test_suppressed_flip_counts_as_tool_decision") now proves a suppressed flip returns a `path: tool` payload with the established mapping.
- **Prevention:** Role-agent runtime must count either mapping-history growth OR suppressed-flip count growth as a successful tool decision. Any change to tool invocation detection needs a focused test where `assign_roles` suppresses a flip and fallback must not run.
