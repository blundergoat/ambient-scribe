---
category: role-agent
last_reviewed: 2026-07-04
---

# Role-Attribution Agent Footguns

## Footgun: The agent must reach Ollama in-network, and the model must actually be pulled

**Status:** active | **Created:** 2026-07-04 | **Evidence:** ACTUAL_MEASURED

- **Files:** `docker-compose.yml` (search: "OLLAMA_HOST=${OLLAMA_HOST:-http://ollama:11434}")
- **Files:** `docker-compose.yml` (search: "# Role inference + summaries default to the bundled Ollama service")
- **Files:** `strands_agents/agents/transcription_agent.py` (search: "OLLAMA_HOST")
- **Files:** `strands_agents/api/server.py` (search: "_is_provider_unreachable")
- **What breaks:** Role inference AND summaries both call Ollama off-GPU. If `OLLAMA_HOST` points at `host.docker.internal:11434`, the agent container cannot reach a host-side Ollama on some Docker/WSL2 setups (it resolves to an IPv6 gateway with nothing listening) → every request logs `role_inference.agent_failed_falling_back_to_heuristic` (scrambled DOCTOR/PATIENT at fixed `confidence: 0.4`, so the UI badge is stuck on "Identifying speakers...") and `summary.agent_failed` → 502. The bundled `ollama` Compose service was also behind a `local` profile, so `docker compose up` never started it even though the provider defaults to `ollama`.
- **Two-Ollama trap:** A host Ollama (`localhost:11434`) and the Compose `ollama` service are easy to confuse. `start-dev.sh` checks the host one; the agent uses the container one. Pull the model into the CONTAINER: `docker compose exec ollama ollama pull qwen3.5:9b` (persists in the `ollama_data` volume). A `summary.agent_failed`/`role_inference.agent_failed` with `duration_ms` in the low tens means the model is missing or unreachable, NOT slow - a real CPU generation is tens of seconds.
- **`.env` override trap:** A stale `.env` `OLLAMA_HOST=http://host.docker.internal:11434` silently overrides the compose default on every recreate, so the agent keeps pointing at the unreachable host address even after the default is fixed. `OLLAMA_HOST` is therefore PINNED to a literal `http://ollama:11434` in `docker-compose.yml` (not `${OLLAMA_HOST:-...}`), so `.env` can no longer reintroduce it. Diagnose with `docker compose exec nemo-agent sh -c 'echo $OLLAMA_HOST'`.
- **Fix:** Pin `OLLAMA_HOST=http://ollama:11434` (literal), start `ollama` by default (no profile), add `nemo-agent depends_on ollama`, publish no host port on it (avoids clashing with a host Ollama on 11434). The browser also pre-flights `GET /agent/model-health` and refuses to start a consultation when the model is unreachable.

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
