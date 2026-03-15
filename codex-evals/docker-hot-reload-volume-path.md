# Docker Hot Reload Volume Path

- Origin: real-history
- Source commit: `9e59867`
- Bug description: The `nemo-agent` dev volume mounted `./strands_agents` at `/app/strands_agents`, which broke imports and caused `ModuleNotFoundError` during container startup.
- Replay prompt: Review `docker-compose.yml` and the Python agent container assumptions. Local dev hot reload is broken with import errors on startup. Fix the mount/runtime mismatch with the narrowest possible change and state how you would validate it.
- Expected outcome: Align the hot-reload mount with the container's import root, avoid unrelated Docker changes, and include `docker compose config` or script-backed validation.
- Failure mode tested: Cross-boundary runtime wiring bug that requires reading Docker and Python assumptions before editing.
