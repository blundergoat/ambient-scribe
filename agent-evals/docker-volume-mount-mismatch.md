# Eval: Docker Volume Mount Path Mismatch

## Bug Description

Docker volume mount `./strands_agents:/app/strands_agents` was incorrect — the Python modules expected to be at `/app`, not `/app/strands_agents`. Container startup failed with `ModuleNotFoundError`. Required reading both `docker-compose.yml` and `docker/nemo/Dockerfile` to understand the expected path.

**Real incident:** commit 9e59867

## Replay Prompt

```
The nemo-agent container is crashing on startup with ModuleNotFoundError. The code works fine outside Docker. Can you fix it?
```

## Expected Outcome

1. Agent enters Debug mode
2. Agent reads BOTH `docker-compose.yml` (volume mounts) AND `docker/nemo/Dockerfile` (WORKDIR, COPY paths)
3. Agent identifies the path mismatch between volume mount and Dockerfile expectations
4. Agent presents diagnosis showing both sides of the boundary before proposing a fix
5. Agent does NOT just change one file without checking the other

## Failure Mode Tested

- **READ**: Must read both sides of the Docker ↔ Python boundary
- **Cross-layer awareness**: Requires understanding Docker volume mount semantics relative to Dockerfile WORKDIR
- **Level 2 escalation**: Docker service change is a cross-boundary issue
