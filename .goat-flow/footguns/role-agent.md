---
category: role-agent
last_reviewed: 2026-04-22
---

# Role-Attribution Agent Footguns

## Footgun: The Ollama role model must support tool calling
**Status:** active | **Created:** 2026-03-21 | **Evidence:** ACTUAL_MEASURED

- **Files:** `.env.example:67-74`
- **Files:** `docker-compose.yml:73-77`
- **Files:** `strands_agents/agents/transcription_agent.py:76-84`
- **Files:** `strands_agents/agents/transcription_agent.py:205-215`
- **Files:** `strands_agents/agents/transcription_agent.py:235-240`
- **Files:** `strands_agents/tools/assign_roles.py:207-244`
- **What breaks:** `assign_roles` is wired as a Strands tool. Models without tool/function calling support fall back to free-text JSON behaviour, which is slower and less reliable.
- **Evidence:** The agent prompt explicitly says it MUST call `assign_roles`, the agent constructor passes `tools=[assign_roles]`, and the default Ollama model is pinned in both `.env.example` and `docker-compose.yml`.
