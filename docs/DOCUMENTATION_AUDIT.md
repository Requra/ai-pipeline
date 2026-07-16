# Documentation audit

Purpose: record the Markdown consolidation of the repository. Audience: maintainers reviewing what was kept, merged, removed, or rewritten. This report is a review artifact and should be deleted after approval unless the team wants to retain the manifest as migration history.

## Result summary

- Original Markdown files inspected: **88**.
- Final canonical prose documents: **15** (root README, documentation index, eleven numbered guides, glossary, and the fixture README).
- Runtime prompt Markdown assets retained: **10**.
- Final audit report: **1**.
- Expected final Markdown total after cleanup: **26**.
- Files archived: **0**.
- Legacy Markdown paths removed from the final tree: **77**, including the old lowercase root README and temporary reports.
- Source, application behavior, dependencies, schemas, migrations, infrastructure, and tests were not changed by this documentation work.

## High-level findings

The repository had several overlapping documentation systems: an older root/docs set, a 50-file `docs/codebase-mastery` handbook, implementation walkthroughs, contracts, readiness reports, and feature notes. They repeated the same pipeline/API/RAG material and sometimes disagreed with source. The most important stale claim was that the graph had 14 nodes; `app/graph/pipeline.py` registers 15. Other stale or unverified claims included a checked-in frontend/backend contract being treated as implementation, Neon-specific deployment language not evidenced by the repository, and a human-in-the-loop pause workflow that is not an active graph feature.

The new documents use source paths and symbols as authority, mark configuration-dependent behavior, and collect limitations in the canonical security, operations, testing, and AI-pipeline guides.

## Main redundancy patterns

- Pipeline architecture was repeated in `production-architecture.md`, `node-reference.md`, `NODE_STATUS.md`, the entire `codebase-mastery` handbook, diagrams, walkthroughs, and feature notes.
- API routes and payloads were repeated across `pipeline-contract.md`, both contract files, the endpoint directory, the Apidog guide, walkthroughs, and codebase-mastery chapters.
- RAG/Redis/pgvector behavior was repeated across three architecture files plus diagrams and the database guide.
- Quality repair, conflict detection, fallback, PII masking, and human review were documented as standalone implementation reports even though their durable value belongs in the AI pipeline, security, or API guides.
- Readiness, production-readiness, changelog, gap-analysis, and completed-plan files mixed temporary project tracking with permanent technical documentation.

## Important stale or misleading claims discovered

| Claim | Verified reality | Corrected in |
|---|---|---|
| “14-node” graph | `build_pipeline()` registers 15 nodes, including `evidence_grounding`. | [04-ai-pipeline.md](04-ai-pipeline.md), [01-codebase-overview.md](01-codebase-overview.md) |
| RAG as a broad conversational architecture | Retrieval is source grounding for evidence; it is not a separate chatbot. | [04-ai-pipeline.md](04-ai-pipeline.md), [glossary.md](glossary.md) |
| Backend/frontend behavior is implemented here | Only the AI service source is present; external callers are documented as boundaries. | [01-codebase-overview.md](01-codebase-overview.md), [05-api-and-data-flow.md](05-api-and-data-flow.md) |
| Human-in-the-loop LangGraph pause | The active route is stateless story regeneration; no active `interrupt()` review graph is registered. | [04-ai-pipeline.md](04-ai-pipeline.md), [05-api-and-data-flow.md](05-api-and-data-flow.md) |
| Redis is durable state | Redis is queue plus six-hour transient input cache; PostgreSQL is the durable path when configured. | [03-system-architecture.md](03-system-architecture.md), [06-database-and-storage.md](06-database-and-storage.md) |
| Callback delivery is a durable completion mechanism | Result persistence happens before a best-effort callback; there is no durable callback outbox/retry. | [04-ai-pipeline.md](04-ai-pipeline.md), [08-deployment-and-operations.md](08-deployment-and-operations.md) |
| Retention settings imply cleanup | Retention variables exist, but no cleanup scheduler is in this repository. | [06-database-and-storage.md](06-database-and-storage.md) |

## Final documentation map

| Document | Purpose |
|---|---|
| `/README.md` | Concise project, setup, and documentation entry point. |
| `/docs/README.md` | Reading order, canonical ownership, and anti-sprawl policy. |
| `/docs/01-codebase-overview.md` | Repository map and component responsibilities. |
| `/docs/02-local-development.md` | Verified local setup, tests, API, Compose, and common failures. |
| `/docs/03-system-architecture.md` | Runtime topology, sequence, boundaries, and runtime modes. |
| `/docs/04-ai-pipeline.md` | End-to-end pipeline, prompt/model/RAG maps, failure matrix, tests, and gaps. |
| `/docs/05-api-and-data-flow.md` | Important routes, payloads, workflows, statuses, and errors. |
| `/docs/06-database-and-storage.md` | Entities, relationships, migrations, vectors, cache, and retention. |
| `/docs/07-testing-debugging-and-observability.md` | Test map, commands, diagnostics, logs, and symptoms. |
| `/docs/08-deployment-and-operations.md` | Compose topology, migrations, release checks, rollback, and risks. |
| `/docs/09-security-and-configuration.md` | Auth, source security, secrets, PII masking, flags, and gaps. |
| `/docs/10-contributor-onboarding.md` | First-day path and safe change checklists. |
| `/docs/11-endpoint-code-interactions.md` | Production endpoint handlers, file transport options, source recovery, and backend integration. |
| `/ai-service/test-fixtures/README.md` | Complementary/conflicting fixture groups, deterministic tests, live upload runner, and expected signals. |
| `/docs/glossary.md` | Requra.AI vocabulary. |

## Original-file change manifest

Accuracy is assessed against current source, tests, config, and repository paths. “Partial” means useful facts were mixed with stale, unverified, or scope-expanding claims.

| Original file | Purpose | Accuracy | Unique value | Overlap | Action | Destination |
|---|---|---|---|---|---|---|
| `ai-service/app/prompts/templates/classify_requirements_v1.md` | Runtime classification prompt | Accurate asset | High | None | Keep | Same path |
| `ai-service/app/prompts/templates/detect_conflicts_v1.md` | Runtime conflict prompt | Accurate asset | High | None | Keep | Same path |
| `ai-service/app/prompts/templates/extract_requirements_v1.md` | Legacy runtime prompt | Accurate asset | Medium | v2 prompt | Keep | Same path |
| `ai-service/app/prompts/templates/extract_requirements_v2.md` | Active extraction prompt | Accurate asset | High | v1 prompt | Keep | Same path |
| `ai-service/app/prompts/templates/generate_user_stories_v1.md` | Legacy runtime prompt | Accurate asset | Medium | v2 prompt | Keep | Same path |
| `ai-service/app/prompts/templates/generate_user_stories_v2.md` | Active story prompt | Accurate asset | High | v1 prompt | Keep | Same path |
| `ai-service/app/prompts/templates/ingest_relevance_v1.md` | Active relevance prompt | Accurate asset | High | Ingest guides | Keep | Same path |
| `ai-service/app/prompts/templates/regenerate_story_v1.md` | Active regeneration prompt | Accurate asset | High | Story feedback docs | Keep | Same path |
| `ai-service/app/prompts/templates/repair_stories_v1.md` | Active repair prompt | Accurate asset | High | Quality repair docs | Keep | Same path |
| `ai-service/app/prompts/templates/summarize_structured_v1.md` | Active summary prompt | Accurate asset | High | Summary guides | Keep | Same path |
| `ai-service/docs/openapi/APIDOG_TESTING_GUIDE.md` | Manual endpoint testing guide | Partial | Medium | API guides/contracts | Merge | `docs/05-api-and-data-flow.md`, `docs/07-testing-debugging-and-observability.md` |
| `ai-service/LANGGRAPH_STUDIO.md` | Studio setup note | Partial | Low | Local setup notes | Merge | `docs/02-local-development.md` |
| `ai-service/STUDIO_CLI.md` | Studio CLI note | Partial | Low | Studio setup note | Merge | `docs/02-local-development.md` |
| `docs/changelog.md` | Feature history | Partial | Low | Git history and reports | Remove | Durable facts folded into canonical docs; git remains history |
| `docs/codebase-mastery/00_START_HERE.md` | Handbook entry point | Partial | Medium | New index/onboarding | Merge | `docs/README.md`, `docs/10-contributor-onboarding.md` |
| `docs/codebase-mastery/01_PRODUCT_AND_PIPELINE_PURPOSE.md` | Product/boundary explanation | Partial | Medium | Overview/architecture | Merge | `docs/01-codebase-overview.md`, `docs/03-system-architecture.md` |
| `docs/codebase-mastery/02_REPOSITORY_MAP.md` | File map | Partial | Medium | Overview | Merge | `docs/01-codebase-overview.md` |
| `docs/codebase-mastery/03_RUNTIME_AND_STARTUP.md` | Startup/runtime flow | Partial | Medium | Local/operations | Merge | `docs/02-local-development.md`, `docs/08-deployment-and-operations.md` |
| `docs/codebase-mastery/04_API_REFERENCE.md` | Endpoint catalog | Partial | Medium | API/OpenAPI | Merge | `docs/05-api-and-data-flow.md` |
| `docs/codebase-mastery/05_API_REQUEST_FLOWS.md` | Request sequences | Partial | Medium | API/pipeline/architecture | Merge | `docs/03-system-architecture.md`, `docs/05-api-and-data-flow.md` |
| `docs/codebase-mastery/06_ACTUAL_PIPELINE_ARCHITECTURE.md` | Active graph reference | Partial | High | Node/reference docs; said 15 correctly | Merge | `docs/04-ai-pipeline.md` |
| `docs/codebase-mastery/07_INTENDED_VS_ACTUAL_ARCHITECTURE.md` | Drift audit | Partial | Medium | Architecture and gap reports; broken experimental path claim | Merge | `docs/04-ai-pipeline.md`, `docs/DOCUMENTATION_AUDIT.md` |
| `docs/codebase-mastery/08_GRAPH_STATE_AND_ROUTING.md` | State/routing reference | Partial | High | Pipeline/node docs | Merge | `docs/04-ai-pipeline.md` |
| `docs/codebase-mastery/09_DATA_CONTRACTS.md` | Cross-layer schema contract | Partial | Medium | API contracts | Merge | `docs/05-api-and-data-flow.md`, `docs/04-ai-pipeline.md` |
| `docs/codebase-mastery/10_LLM_MODELS_AND_PROMPTS.md` | Model/prompt reference | Partial | High | Pipeline/prompt guide | Merge | `docs/04-ai-pipeline.md` |
| `docs/codebase-mastery/11_RAG_AND_RETRIEVAL.md` | Retrieval reference | Partial | High | RAG architecture files | Merge | `docs/04-ai-pipeline.md`, `docs/06-database-and-storage.md` |
| `docs/codebase-mastery/12_DOCUMENT_AND_AUDIO_PROCESSING.md` | Ingest/STT reference | Partial | High | Node/pipeline/security docs | Merge | `docs/04-ai-pipeline.md`, `docs/09-security-and-configuration.md` |
| `docs/codebase-mastery/13_BACKEND_INTEGRATION.md` | Backend integration | Partial | High | Production/API docs; external claims | Merge | `docs/03-system-architecture.md`, `docs/05-api-and-data-flow.md` |
| `docs/codebase-mastery/14_ERROR_HANDLING_AND_OBSERVABILITY.md` | Error/probe guide | Partial | Medium | Testing/operations docs | Merge | `docs/07-testing-debugging-and-observability.md` |
| `docs/codebase-mastery/15_CONFIGURATION_AND_ENVIRONMENT.md` | Env reference | Partial | High | Security/local docs | Merge | `docs/09-security-and-configuration.md` |
| `docs/codebase-mastery/16_TESTING_AND_DEBUGGING.md` | Test guide | Partial | Medium | Testing docs | Merge | `docs/07-testing-debugging-and-observability.md` |
| `docs/codebase-mastery/17_DEPLOYMENT_AND_OPERATIONS.md` | Deployment guide | Partial | Medium | Production/verification docs | Merge | `docs/08-deployment-and-operations.md` |
| `docs/codebase-mastery/18_ARCHITECTURAL_RISKS_AND_TECH_DEBT.md` | Risk report | Partial | Medium | Audit/operations/security docs | Merge | `docs/04-ai-pipeline.md`, `docs/08-deployment-and-operations.md`, `docs/09-security-and-configuration.md` |
| `docs/codebase-mastery/19_SAFE_CHANGE_GUIDE.md` | Change checklist | Partial | High | Onboarding/rules | Merge | `docs/10-contributor-onboarding.md` |
| `docs/codebase-mastery/20_GLOSSARY.md` | Terms | Partial | Medium | Glossaries | Merge | `docs/glossary.md` |
| `docs/codebase-mastery/21_NEW_DEVELOPER_LEARNING_PATH.md` | Learning path | Partial | Medium | Onboarding/index | Merge | `docs/10-contributor-onboarding.md` |
| `docs/codebase-mastery/diagrams/api-sequences.md` | API Mermaid diagram | Partial | Low | Architecture/API flows | Merge | `docs/03-system-architecture.md` |
| `docs/codebase-mastery/diagrams/error-flow.md` | Error diagram | Partial | Low | Pipeline failure matrix | Merge | `docs/04-ai-pipeline.md` |
| `docs/codebase-mastery/diagrams/graph-flow.md` | Graph diagram | Partial | Medium | Pipeline architecture | Merge | `docs/04-ai-pipeline.md` |
| `docs/codebase-mastery/diagrams/rag-flow.md` | RAG diagram | Partial | Low | Pipeline retrieval section | Merge | `docs/04-ai-pipeline.md` |
| `docs/codebase-mastery/diagrams/README.md` | Diagram index | Low | Low | New canonical docs | Remove | No unique content |
| `docs/codebase-mastery/diagrams/system-context.md` | System context diagram | Partial | Medium | System architecture | Merge | `docs/03-system-architecture.md` |
| `docs/codebase-mastery/endpoints/api-testing-playbook.md` | Curl/API playbook | Partial | Medium | API/testing docs | Merge | `docs/02-local-development.md`, `docs/07-testing-debugging-and-observability.md` |
| `docs/codebase-mastery/endpoints/health-and-operational-endpoints.md` | Probe reference | Accurate | Low | API/testing/operations | Merge | `docs/05-api-and-data-flow.md`, `docs/07-testing-debugging-and-observability.md` |
| `docs/codebase-mastery/endpoints/internal-endpoints.md` | Internal route reference | Partial | Medium | API contracts | Merge | `docs/05-api-and-data-flow.md` |
| `docs/codebase-mastery/endpoints/public-endpoints.md` | Demo route reference | Partial | Low | API/local docs | Merge | `docs/05-api-and-data-flow.md`, `docs/02-local-development.md` |
| `docs/codebase-mastery/endpoints/README.md` | Endpoint index | Low | Low | API index/OpenAPI | Remove | No unique content |
| `docs/codebase-mastery/nodes/additional-active-nodes.md` | Supporting node notes | Partial | High | Pipeline/node docs | Merge | `docs/04-ai-pipeline.md` |
| `docs/codebase-mastery/nodes/classify.md` | Classify node | Partial | Medium | Pipeline/LLM docs | Merge | `docs/04-ai-pipeline.md` |
| `docs/codebase-mastery/nodes/extract.md` | Extract node | Partial | Medium | Pipeline/LLM docs | Merge | `docs/04-ai-pipeline.md` |
| `docs/codebase-mastery/nodes/format.md` | Format node | Partial | Medium | Pipeline/contracts | Merge | `docs/04-ai-pipeline.md`, `docs/05-api-and-data-flow.md` |
| `docs/codebase-mastery/nodes/generate.md` | Generate node | Partial | Medium | Pipeline/quality docs | Merge | `docs/04-ai-pipeline.md` |
| `docs/codebase-mastery/nodes/ingest.md` | Ingest node | Partial | High | Pipeline/PII docs | Merge | `docs/04-ai-pipeline.md`, `docs/09-security-and-configuration.md` |
| `docs/codebase-mastery/nodes/README.md` | Node index | Low | Low | Pipeline index | Remove | No unique content |
| `docs/codebase-mastery/nodes/summarize.md` | Summary node | Partial | Medium | Pipeline/prompt docs | Merge | `docs/04-ai-pipeline.md` |
| `docs/codebase-mastery/nodes/transcribe.md` | Transcription node | Partial | High | Pipeline/audio docs | Merge | `docs/04-ai-pipeline.md` |
| `docs/codebase-mastery/README.md` | Handbook index | Partial | Low | New docs index | Remove | Replaced by `docs/README.md` |
| `docs/codebase-mastery/walkthroughs/audio-request-walkthrough.md` | Audio trace | Partial | Medium | AI/API flow | Merge | `docs/04-ai-pipeline.md`, `docs/05-api-and-data-flow.md` |
| `docs/codebase-mastery/walkthroughs/document-request-walkthrough.md` | Document trace | Partial | Medium | AI/API flow | Merge | `docs/04-ai-pipeline.md`, `docs/05-api-and-data-flow.md` |
| `docs/codebase-mastery/walkthroughs/rag-request-walkthrough.md` | RAG trace | Partial | Medium | AI pipeline/RAG docs | Merge | `docs/04-ai-pipeline.md` |
| `docs/codebase-mastery/walkthroughs/text-request-walkthrough.md` | Text trace | Partial | Medium | AI/API flow | Merge | `docs/04-ai-pipeline.md`, `docs/05-api-and-data-flow.md` |
| `docs/codebase-mastery/walkthroughs/transcript-request-walkthrough.md` | Transcript trace | Partial | Medium | AI/API flow | Merge | `docs/04-ai-pipeline.md`, `docs/05-api-and-data-flow.md` |
| `docs/collaboration_rules.md` | Team process rules | Partial | Medium | Root rules/onboarding | Merge | `docs/README.md`, `docs/10-contributor-onboarding.md` |
| `docs/contracts/mvp-results-dashboard-api-contract.md` | External dashboard contract | Unknown/partial | Medium | API contract; external backend absent | Merge | `docs/05-api-and-data-flow.md` boundary note |
| `docs/contracts/pipeline-response-v1.md` | V1 result contract | Partial | High | Schemas/format/API docs | Merge | `docs/04-ai-pipeline.md`, `docs/05-api-and-data-flow.md` |
| `docs/db-documentation.md` | Schema/Neon operations guide | Partial | High | Storage/architecture docs; Neon not verified | Merge | `docs/06-database-and-storage.md` |
| `docs/enhanced-pii-masking.md` | PII masking report | Partial | High | Ingest/security docs | Merge | `docs/04-ai-pipeline.md`, `docs/09-security-and-configuration.md` |
| `docs/feature_gap_analysis.md` | Completed gap analysis | Temporary | Medium | Readiness/risk reports | Remove | Durable gaps preserved in canonical docs |
| `docs/human-in-the-loop-review-workflow.md` | Review/regeneration proposal | Misleading/partial | Medium | Active stateless regeneration and future workflow | Merge | `docs/04-ai-pipeline.md`, `docs/05-api-and-data-flow.md` |
| `docs/llm-fallback-and-multi-document-awareness.md` | Implementation walkthrough | Partial | High | LLM/RAG/pipeline docs | Merge | `docs/04-ai-pipeline.md` |
| `docs/NODE_STATUS.md` | Node status report | Stale | Medium | Node/pipeline docs; says 14 | Remove | Current status in `docs/04-ai-pipeline.md` |
| `docs/node-reference.md` | Node reference | Stale | High | Codebase mastery/pipeline docs; says 14 | Merge | `docs/04-ai-pipeline.md` |
| `docs/pipeline-contract.md` | Input contract | Partial | High | API schemas/contracts | Merge | `docs/05-api-and-data-flow.md` |
| `docs/production-architecture.md` | Production architecture | Partial | High | Most architecture/ops docs; several unverified claims | Rewrite | `docs/03-system-architecture.md`, `docs/04-ai-pipeline.md`, `docs/08-deployment-and-operations.md` |
| `docs/production-readiness-report-2026-07-05.md` | Point-in-time readiness report | Temporary/historical | Medium | Verification reports | Remove | Risks and unresolved external checks preserved |
| `docs/prompts/prompt-management.md` | Prompt process | Partial | Medium | Pipeline/onboarding docs and tests | Merge | `docs/04-ai-pipeline.md`, `docs/10-contributor-onboarding.md` |
| `docs/quality-guided-repair-pass.md` | Repair implementation report | Partial | High | Pipeline/quality docs; old node count | Merge | `docs/04-ai-pipeline.md` |
| `docs/rag-and-redis.md` | RAG/Redis guide | Partial | High | RAG/storage/architecture docs | Merge | `docs/04-ai-pipeline.md`, `docs/06-database-and-storage.md` |
| `docs/rag-grounding-architecture.md` | RAG architecture | Partial | High | RAG/production docs | Merge | `docs/04-ai-pipeline.md` |
| `docs/README.md` | Old documentation index | Stale/partial | Low | All docs | Rewrite | Same path |
| `docs/semantic-conflict-detection.md` | Conflict feature report | Partial | High | Pipeline/RAG/config docs; says 14 | Merge | `docs/04-ai-pipeline.md` |
| `docs/verification/E2E_TEST_MATRIX.md` | Point-in-time E2E matrix | Temporary | Medium | Testing/readiness reports | Remove | Test coverage and external limits preserved |
| `docs/verification/KNOWN_LIMITATIONS.md` | Limitations report | Partial | High | Operations/security/pipeline docs | Merge | `docs/04-ai-pipeline.md`, `docs/08-deployment-and-operations.md`, `docs/09-security-and-configuration.md` |
| `docs/verification/PRODUCTION_READINESS_REPORT.md` | Point-in-time readiness | Temporary | Medium | Other readiness reports | Remove | Genuine gaps preserved in canonical docs |
| `docs/verification/RELEASE_RUNBOOK.md` | Release checklist | Partial | Medium | Operations/local docs | Merge | `docs/08-deployment-and-operations.md`, `docs/07-testing-debugging-and-observability.md` |
| `readme.md` | Old root engineering hub | Partial/stale | High | All onboarding/architecture docs | Rewrite/rename | `README.md` |
| `rules.md` | Development rules | Partial | Medium | Collaboration/onboarding docs | Merge | `docs/README.md`, `docs/10-contributor-onboarding.md` |
| `test-documents/README.md` | Sample input inventory | Accurate | Low | Local development/testing docs | Merge | `docs/02-local-development.md`, `docs/07-testing-debugging-and-observability.md` |

## Files kept, rewritten, merged, archived, and removed

- **Kept:** the ten prompt templates because application code loads them by registry id; they are source assets, not redundant prose.
- **Rewritten:** root `README.md` (from `readme.md`) and `docs/README.md`.
- **Created:** the ten numbered guides, `docs/glossary.md`, and this report.
- **Merged/removed:** all other old prose/reference files listed above. The unique implementation facts are represented in the canonical documents and linked to source symbols.
- **Archived:** none. No old file was judged to be a sufficiently valuable historical migration or incident record to justify `docs/archive/`.

## Remaining uncertainties

1. The external backend/frontend implementation is not in this repository, so cross-service claims can only be checked against AI-service schemas, tests, and the checked-in OpenAPI artifact.
2. A live PostgreSQL/Redis/RQ deployment, provider call, callback receiver, and external source recovery endpoint were not exercised during this audit.
3. Retention configuration exists, but the cleanup mechanism is not present in the checked-in code.
4. The checked-in OpenAPI JSON is treated as an artifact; the audit did not regenerate it from a running server.

## Maintenance recommendation

Keep the canonical set small. When behavior changes, update the owner document and its focused tests in the same pull request. Delete completed plans and generated reports after durable facts are captured. Treat prompt templates, OpenAPI JSON, migrations, and test fixtures as executable/source artifacts with their own change review, not as excuses to add another prose guide.

## Validation performed

- `poetry run pytest -q` from `ai-service`: **321 passed, 1 skipped**.
- `poetry run python -c "from app.graph.pipeline import build_pipeline; ..."`: graph compiled successfully.
- Live FastAPI route inventory matched the checked-in OpenAPI artifact exactly: no path differences.
- `docker compose config --quiet`: succeeded.
- Markdown relative-link check: **0 broken file links**.
- Markdown fenced-code check: **0 unbalanced fences**.
- Canonical source-path existence check: **0 missing referenced implementation files**.
- No Mermaid parser was installed in the workspace; Mermaid blocks were checked for balanced fences and reviewed against the actual graph/schema relationships, but an independent semantic parser run was not available.
