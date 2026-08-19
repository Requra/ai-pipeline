# Documentation Index

Purpose: Provide one maintained entry point to the verified Requra.AI codebase documentation. Audience: New developers, AI and backend engineers, reviewers, and maintainers.

## Recommended reading order

1. [01-codebase-overview.md](01-codebase-overview.md) — What is in this repository and where to start tracing.
2. [02-local-development.md](02-local-development.md) — Verified setup, commands, and local modes.
3. [03-system-architecture.md](03-system-architecture.md) — Runtime boundaries and job flow.
4. [04-ai-pipeline.md](04-ai-pipeline.md) — The complete implemented AI pipeline (13 nodes).
5. [05-api-and-data-flow.md](05-api-and-data-flow.md) — HTTP workflows, input types, and contracts.
6. [06-database-and-storage.md](06-database-and-storage.md) — Durable entities, stores, pooling, and retention.
7. [07-testing-debugging-and-observability.md](07-testing-debugging-and-observability.md) — Tests, logs, and diagnostics.
8. [08-deployment-and-operations.md](08-deployment-and-operations.md) — Compose, workers, CI gates, migrations, and operations.
9. [09-security-and-configuration.md](09-security-and-configuration.md) — Trust boundaries, secrets, limits, and feature flags.
10. [10-contributor-onboarding.md](10-contributor-onboarding.md) — Safe change paths and documentation ownership.
11. [11-endpoint-code-interactions.md](11-endpoint-code-interactions.md) — Code-level endpoint handlers and file interactions.
12. [glossary.md](glossary.md) — Project vocabulary.

## Canonical ownership

| Topic | Canonical document |
|---|---|
| Repository responsibilities and code map | [01-codebase-overview.md](01-codebase-overview.md) |
| Setup and local commands | [02-local-development.md](02-local-development.md) |
| Services, queues, stores, and trust boundaries | [03-system-architecture.md](03-system-architecture.md) |
| Node graph, prompts, retrieval, model calls, persistence, and failure paths | [04-ai-pipeline.md](04-ai-pipeline.md) |
| HTTP routes and end-to-end request workflows | [05-api-and-data-flow.md](05-api-and-data-flow.md) |
| PostgreSQL schema, pgvector, Redis cache, and retention | [06-database-and-storage.md](06-database-and-storage.md) |
| Tests, logs, readiness, and debugging | [07-testing-debugging-and-observability.md](07-testing-debugging-and-observability.md) |
| Deployment, CI release gates, and operations | [08-deployment-and-operations.md](08-deployment-and-operations.md) |
| Configuration, authentication, privacy, and security limits | [09-security-and-configuration.md](09-security-and-configuration.md) |
| Contributor workflow and safe modifications | [10-contributor-onboarding.md](10-contributor-onboarding.md) |
| HTTP endpoint handlers, code paths, and file interactions | [11-endpoint-code-interactions.md](11-endpoint-code-interactions.md) |
| Grouped pipeline fixtures and live upload validation | [ai-service/test-fixtures/README.md](../ai-service/test-fixtures/README.md) |
| Definitions | [glossary.md](glossary.md) |
| Runtime prompt text | `ai-service/app/prompts/templates/*.md` (source assets, not prose documentation) |

The generated OpenAPI contract is [docs/openapi.json](openapi.json). Do not manually duplicate every generated endpoint here.

## Documentation maintenance policy

- Every subject has one canonical document.
- Other documents link to the canonical owner instead of repeating it.
- Temporary implementation plans must not remain indefinitely.
- Completed plans are removed after durable knowledge is incorporated into canonical docs.
- Architecture changes update the corresponding canonical document in the same change.
- New Markdown files require a clearly distinct purpose.
- Historical documents belong in `docs/archive/` only when they retain genuine migration, decision, or incident value.
- Obsolete docs are removed, not kept "just in case."
