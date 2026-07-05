# Requra AI Pipeline Technical Documentation

Welcome to the technical documentation index for the Requra AI Pipeline repository. 

This directory contains the structural architecture plans, API schemas, prompt management guides, and implementation history for the FastAPI + LangGraph microservice.

---

## 🗺️ Documentation Directory Map

### 🚀 Getting Started (Read First)
* **[production-architecture.md](production-architecture.md)**: The definitive guide to the production deployment. Read this first to understand the FastAPI API server, Redis background queue workers, PostgreSQL/pgvector durability, and security features.
* **[rag-grounding-architecture.md](rag-grounding-architecture.md)**: Architectural documentation explaining why the pipeline uses RAG for source grounding rather than a conversational QA chatbot. Covers tokenization, BM25 scoring, pgvector semantic search, and the deduplication Jaccard formulas.

### 🧩 System Guides & Reference
* **[node-reference.md](node-reference.md)**: Reference guide mapping out the compiled **14-node LangGraph structure**, individual node responsibilities, routing logic, and standard warning codes.
* **[NODE_STATUS.md](NODE_STATUS.md)**: Real-time implementation status report across all 14 nodes, detailing what is integrated and active.
* **[collaboration_rules.md](collaboration_rules.md)**: Collaboration guidelines, Scrum workflows, state dictionary safety rules, and coding best practices for developers.
* **[prompts/prompt-management.md](prompts/prompt-management.md)**: Centralized management and protection instructions for LLM prompt templates, snapshot protection testing, and template versioning.

### 📜 API Contracts
* **[pipeline-contract.md](pipeline-contract.md)**: API request payload specifications for both development (local/synchronous) and internal production (asynchronous background task) endpoints.
* **[contracts/pipeline-response-v1.md](contracts/pipeline-response-v1.md)**: Frozen V1 output contract (`JobResult`) schema details and JSON examples (Golden Success, Partial Failure, and System Failure).

### 📈 Decisions & History
* **[adr/](adr/)**: Directory containing Architecture Decision Records (ADRs) tracking foundational design and framework selections.
* **[changelog.md](changelog.md)**: Chronological feature log and git transition changes mapping commit-by-commit RAG enhancements.
