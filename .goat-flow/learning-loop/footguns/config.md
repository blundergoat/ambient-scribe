---
category: config
last_reviewed: 2026-07-04
---

# Config, Networking, and Deployment Footguns

## Footgun: Browser-facing WebSocket and Mercure URLs are passed straight through
**Status:** active | **Created:** 2026-03-21 | **Evidence:** ACTUAL_MEASURED

- **Files:** `.env.example` (search: "NEMO_WEBSOCKET_URL=ws://localhost:48101")
- **Files:** `.env.example` (search: "MERCURE_PUBLIC_URL=http://localhost:48137/.well-known/mercure")
- **Files:** `docker-compose.yml` (search: "NEMO_WEBSOCKET_URL=ws://localhost:${AGENT_PORT:-48101}")
- **Files:** `src/Controller/ScribeController.php` (search: "$mercureUrl = $this->getParameter('mercure_url')")
- **Files:** `src/Controller/ScribeController.php` (search: "'ws_url' => $wsUrl")
- **Files:** `templates/scribe/index.html.twig` (search: "const CONFIG =")
- **Files:** `public/js/scribe-recording.js` (search: "new WebSocket(`${CONFIG.wsUrl}/ws/transcribe/")
- **Files:** `public/js/scribe-recording.js` (search: "streams = new StreamOrchestrator(CONFIG.mercureUrl)")
- **What breaks:** Host-only defaults like `localhost:48101` and `localhost:48137` work on the developer machine but fail for remote clients or alternate hostnames unless every layer is overridden together.
- **Evidence:** Symfony injects `ws_url` and `mercure_url` directly into `CONFIG`, and `public/js/scribe-recording.js` uses those values as-is for the WebSocket and Mercure subscriptions.

## Footgun: Terraform still advertises DynamoDB while runtime persists only memory or SQLite
**Status:** active | **Created:** 2026-03-21 | **Evidence:** ACTUAL_MEASURED

- **Files:** `infra/terraform/environments/prod/main.tf` (search: "DYNAMODB_TABLE")
- **Files:** `infra/terraform/environments/prod/main.tf` (search: "MERCURE_PUBLIC_URL")
- **Files:** `strands_agents/api/server.py` (search: "def create_storage_backend")
- **Files:** `strands_agents/session.py` (search: "class SessionStore")
- **Files:** `strands_agents/storage.py` (search: "class SqliteBackend")
- **What breaks:** Production infrastructure exports a DynamoDB table name, but runtime persistence only switches between the in-memory `SessionStore` and local SQLite. There is no DynamoDB-backed runtime path.
- **Evidence:** Terraform sets `DYNAMODB_TABLE`, `create_storage_backend()` chooses only `SessionStore` or `SqliteBackend`, and the in-memory store still documents restart data loss.

## Footgun: Bind-mounted local dev can hide image-only runtime issues
**Status:** active | **Created:** 2026-03-21 | **Evidence:** ACTUAL_MEASURED

- **Files:** `Dockerfile` (search: "COPY . /app/")
- **Files:** `docker-compose.yml` (search: "./strands_agents:/app")
- **Files:** `docker-compose.yml` (search: ".:/app  # Hot reload in dev")
- **What breaks:** Local Compose uses bind mounts for both the app and agent, so hot reload can look healthy even when the built images would still ship stale files or broken import paths.
- **Evidence:** The Dockerfile bakes the app into `/app`, the agent image expects `/app`, and Compose overrides both services with host mounts during local development.
