---
category: role-agent
last_reviewed: 2026-07-04
---

# Role-Attribution Agent Footguns

## Footgun: The Ollama role model must support tool calling
**Status:** active | **Created:** 2026-03-21 | **Evidence:** ACTUAL_MEASURED

- **Files:** `.env.example` (search: "ROLE_AGENT_OLLAMA_MODEL=qwen2.5:14b")
- **Files:** `docker-compose.yml` (search: "ROLE_AGENT_MODEL_PROVIDER=${ROLE_AGENT_MODEL_PROVIDER:-ollama}")
- **Files:** `strands_agents/agents/transcription_agent.py` (search: "You MUST call the assign_roles tool")
- **Files:** `strands_agents/agents/transcription_agent.py` (search: "from tools.assign_roles import assign_roles")
- **Files:** `strands_agents/agents/transcription_agent.py` (search: "tools=[assign_roles]")
- **Files:** `strands_agents/tools/assign_roles.py` (search: "def assign_roles")
- **What breaks:** `assign_roles` is wired as a Strands tool. Models without tool/function calling support fall back to free-text JSON behaviour, which is slower and less reliable.
- **Evidence:** The agent prompt explicitly says it MUST call `assign_roles`, the agent constructor passes `tools=[assign_roles]`, and the default Ollama model is pinned in both `.env.example` and `docker-compose.yml`.
