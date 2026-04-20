---
category: config
---

# Config, Networking, and Deployment Footguns

## Footgun: Browser-facing WebSocket and Mercure URLs are passed straight through

- **Files:** `.env.example:19-23`
- **Files:** `.env.example:79-81`
- **Files:** `docker-compose.yml:119-123`
- **Files:** `src/Controller/ScribeController.php:54-56`
- **Files:** `src/Controller/ScribeController.php:81-90`
- **Files:** `templates/scribe/index.html.twig:825-833`
- **Files:** `public/js/scribe.js:373-380`
- **Files:** `public/js/scribe.js:530-535`
- **What breaks:** Host-only defaults like `localhost:48101` and `localhost:48137` work on the developer machine but fail for remote clients or alternate hostnames unless every layer is overridden together.
- **Evidence:** Symfony injects `ws_url` and `mercure_url` directly into `CONFIG`, and `public/js/scribe.js` uses those values as-is for the WebSocket and Mercure subscriptions.

## Footgun: Terraform still advertises DynamoDB while runtime persists only memory or SQLite

- **Files:** `infra/terraform/environments/prod/main.tf:82-90`
- **Files:** `infra/terraform/environments/prod/main.tf:142-146`
- **Files:** `strands_agents/api/server.py:298-310`
- **Files:** `strands_agents/session.py:28-29`
- **Files:** `strands_agents/storage.py:54-76`
- **What breaks:** Production infrastructure exports a DynamoDB table name, but runtime persistence only switches between the in-memory `SessionStore` and local SQLite. There is no DynamoDB-backed runtime path.
- **Evidence:** Terraform sets `DYNAMODB_TABLE`, `create_storage_backend()` chooses only `SessionStore` or `SqliteBackend`, and the in-memory store still documents restart data loss.

## Footgun: Bind-mounted local dev can hide image-only runtime issues

- **Files:** `Dockerfile:36-43`
- **Files:** `docker-compose.yml:84-87`
- **Files:** `docker-compose.yml:108-114`
- **What breaks:** Local Compose uses bind mounts for both the app and agent, so hot reload can look healthy even when the built images would still ship stale files or broken import paths.
- **Evidence:** The Dockerfile bakes the app into `/app`, the agent image expects `/app`, and Compose overrides both services with host mounts during local development.
