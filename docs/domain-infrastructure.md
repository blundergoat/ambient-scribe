# Domain: Infrastructure

Docker Compose local topology and AWS deployment overview. For full deployment commands, see `docs/deployment.md`. For Terraform details, see `docs/terraform.md`.

## Docker Compose Topology

3 services defined in `docker-compose.yml`:

| Service | Image | Port (host:container) | GPU | Purpose |
|---|---|---|---|---|
| `nemo-agent` | `docker/nemo/Dockerfile` | 48101:8000 | Yes (1x NVIDIA) | FastAPI + NeMo inference + Strands agent |
| `app` | `./Dockerfile` | 48082:8080 | No | Symfony PHP UI + session management |
| `mercure` | `dunglas/mercure` | 48137:3701 | No | SSE hub for real-time segment delivery |

Communication: `app` → `nemo-agent` via `http://nemo-agent:8000`. `nemo-agent` → `mercure` via `http://mercure:3701`. Browser → `mercure` via `http://localhost:48137`.

**Requires NVIDIA Container Toolkit** for GPU passthrough to `nemo-agent`.

## NeMo Container

Base: `nvcr.io/nvidia/nemo:26.02` + pinned `nemo_toolkit[asr]==2.7.3`. See `docker/nemo/Dockerfile`.

Must include:
- ffmpeg (for WebM → WAV conversion)
- NeMo models pre-downloaded at build time (multi-GB, cached in `/root/.cache/huggingface/hub/`)
- Python dependencies from `strands_agents/requirements.txt`

See `docs/troubleshooting.md` for container version compatibility (RTX 5080 sm_120 requires NeMo 25.09+).

## Environment Variable Synchronization

These variables must be consistent across services. Mismatch causes silent failures.

| Variable | nemo-agent | app | mercure | Notes |
|---|---|---|---|---|
| `MERCURE_JWT_SECRET` | — | Yes (signing) | Yes (verification) | Must match. ≥32 chars. |
| `MERCURE_PUBLISHER_JWT` | Yes (pre-signed) | — | — | JWT signed with MERCURE_JWT_SECRET |
| `MERCURE_HUB_URL` | `http://mercure:3701/...` | — | — | Internal Docker network |
| `MERCURE_URL` | — | `http://mercure:3701/...` | — | PHP server-side publish URL |
| `MERCURE_PUBLIC_URL` | — | `http://localhost:48137/...` | — | Browser-side SSE URL |
| `NEMO_WEBSOCKET_URL` | — | `ws://localhost:48101` | — | Browser-side WebSocket URL |
| `ROLE_AGENT_MODEL_PROVIDER` | `bedrock` | — | — | Must be bedrock or ollama, NOT local GPU |

See `.goat-flow/learning-loop/footguns/runtime.md` for JWT / Mercure publish failure debugging.

## Terraform Structure

```
infra/terraform/
├── bootstrap/              # One-time: S3 state bucket + DynamoDB lock
├── environments/prod/      # Root module (wires all modules together)
└── modules/                # 15 independent modules
    ├── network/            # VPC, subnets (self-contained or BYO from SSM)
    ├── ecs/                # ECS cluster + task definition
    ├── ecs-service/        # Fargate service with circuit breaker
    ├── ecr/                # Container registries
    ├── iam/                # Task roles + GitHub OIDC
    ├── alb/                # Application Load Balancer
    ├── dns/                # Route 53 + ACM certificate
    ├── dynamodb/           # Session persistence (TTL auto-expiry)
    ├── secrets/            # Secrets Manager
    ├── security/           # Security groups (ALB + ECS)
    ├── waf/                # WAF rate limiting
    ├── alarms/             # CloudWatch alarms
    └── observability/      # CloudWatch log groups
```

**Self-contained design:** The network module can create its own VPC or consume an existing one from SSM Parameter Store (`blundergoat-infra`). No hard dependency on external Terraform state.

## Deployment

**ECS Fargate sidecar pattern:** All three services (app, nemo-agent, mercure) run as containers in a single ECS task, communicating over localhost.

```bash
./scripts/deploy.sh              # Build + push both images + ECS redeploy
./scripts/deploy.sh agent        # Agent image only
./scripts/deploy.sh app          # App image only
./scripts/terraform.sh plan      # Preview infrastructure changes
./scripts/terraform.sh apply     # Apply infrastructure changes
```

See `docs/deployment.md` for the full deployment flow and `docs/infrastructure.md` for the AWS architecture diagram.
