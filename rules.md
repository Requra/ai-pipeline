# Requra.AI Pipeline Development Rules

This document establishes the mandatory standards and guidelines for all development on the Requra.AI pipeline, specifically targeting our LangGraph implementation. All developers and AI agents must adhere to these rules strictly.

---

## 1. LangGraph Framework & Skill Adherence
- **Mandatory Skill Utilization**: All LangGraph development, maintenance, and refactoring **must** use the installed LangGraph-related skills and patterns available in the environment:
  - **`langgraph-fundamentals`**: For graph construction, node definitions, static/conditional edges, `Command` patterns, parallel orchestration (the `Send` API), and basic updates.
  - **`langgraph-persistence`**: For state persistence, conversation memory, history traversal (time travel), and subgraph scoping.
  - **`langgraph-human-in-the-loop`**: For human-in-the-loop integration, approval gates, validation workflows, and the 4-tier error handling strategy.
  - **`langchain-rag`**: For document parsing, chunking, embeddings, and vector stores.

---

## 2. Graph State Safety & Type Contracts
- **Pydantic State Validation**: The pipeline must operate on a typed, structured state (`PipelineState`) backed by Pydantic models. Dict-only states are forbidden beyond simple routing control keys.
- **State Mutability**: Nodes must return partial updates as dictionaries containing *only* modified keys. Modifying the state object directly in-place and returning the full state is strictly prohibited.
- **State Reducers**: Any state field that accumulates lists (e.g., lists of requirements, stories, errors, warnings) **must** utilize an explicit reducer (such as `Annotated[list, operator.add]`) to avoid overwriting results, especially when running concurrent nodes or fanning out/in.
- **API Contract Enforcement**: No endpoint may return raw LangGraph internal state. All client responses must pass `JobResult` validation to prevent unstable contracts and downstream breakage.

---

## 3. Conditional Routing & Graph Correctness
- **Deterministic Routing**: Router functions (used in conditional edges) must evaluate state fields deterministically. All conditional routes must have explicit fallback nodes (such as the final formatter or `END`) to prevent graph lockups or infinite loops.
- **Explicit Edge Declarations**: When using dynamic routing via `Command`, the list of potential destination nodes must be explicitly annotated using `Command[Literal["node_a", "node_b"]]` (Python) or `{ ends: ["node_a", "node_b"] }` (TypeScript).
- **No Orphan Nodes**: All nodes added to the graph must be fully reachable from the entry point and have clear paths to `END`.

---

## 4. Error Handling & Recovery Protocol
- **The 4-Tier Error Handling Strategy**:
  1. **Transient Errors**: Implement a `RetryPolicy` on nodes making external API requests (e.g. LLM providers, transcription providers) to handle network drops and rate limits.
  2. **LLM-Recoverable Errors**: Use structured outputs with validation and allow the LLM to recover from its own parsing issues where possible.
  3. **User-Fixable Errors / Interrupts**: Pause execution using `interrupt()` for human validation when low-confidence items, critical quality issues, or parsing ambiguities are detected.
  4. **Unexpected Failures**: Gracefully propagate unexpected developer exceptions while maintaining partial results in the final formatted response.
- **No Silent Failures**: All caught errors must be logged structurally and appended to the state's `error` or `warnings` lists with consistent prefixes (e.g. `INGEST_FAILED`, `EXTRACT_LLM_FAILURE`).
- **No Hallucinated Fallbacks**: Hardcoded or hallucinated fake lists (such as returning a dummy functional requirement or dummy story on LLM failure) are strictly prohibited. Nodes must fail cleanly, raise warnings, and return empty collections or set execution flags.

---

## 5. Quality Control, Grounding & Cardinality Mapping
- **Requirement Extraction Rule**: Extraction must capture Functional Requirements (FR), Non-Functional Requirements (NFR), Business Rules (BR), Constraints, Assumptions, Open Questions, and explicit Out-of-Scope items. Do not discard NFR/BR before classification.
- **Strict Evidence Verification**: No production requirement may exist without at least one `EvidenceSpan`. No user story may be returned unless it maps to source requirements with evidence.
- **Requirement-to-Story Mapping Cardinality**: Mappings must support:
  - **one requirement → one story** (One-to-one mapping)
  - **one requirement → multiple stories** (One-to-many mapping)
  - **multiple requirements → one story** (Many-to-one mapping)
  - **requirement → attached acceptance criteria** (Requirement integrated directly as criteria in another story)
  - **requirement → non-story requirement** (Requirement tracked but does not warrant a story)
  - **requirement → needs_review** (Flagged for manual review)
  Every extracted/classified requirement must have a corresponding `RequirementCoverage` record. Every story must map back to `source_requirement_ids` and evidence spans.
- **Self-Correction (Repair)**: If validation fails, route through a repair node that attempts to fix issues using target context without hallucinating content, or mark the state status as `needs_review`.

---

## 6. Dependency Safety
- **Explicit Declaration**: Every imported third-party package must be declared in `pyproject.toml`.
- **System Packages**: Every system dependency such as `ffmpeg` must be explicitly installed in the `Dockerfile`.
- **No Hidden Runtime Dependencies**: No execution path may depend on packages not declared in `pyproject.toml` or system dependencies not present in the container image.

---

## 7. Production Observability
- **Structured JSON Logging**: Production observability must be structured; print-only observability is strictly forbidden.
- **Mandatory Telemetry Fields**: Every node must log structured events containing:
  - `job_id`
  - `trace_id`
  - `node_name`
  - `status`
  - `duration_ms`
  - `input_count` / `input_size`
  - `output_count`
  - `warning_count`
  - `error_code`
  - `provider` / `model` (when applicable)

---

## 8. Safe Rollback
- **No Re-enabling Unsafe Behaviors**: Rollback procedures must never revert to known unsafe behavior (e.g. trusting client-supplied file types without detection, generating mock fake requirements, returning raw internal state, or using stdout-only print logs).
- **Graceful Feature Degradation**: Rollbacks may degrade advanced capabilities (e.g. bypassing repair loops, disabling deduplication, disabling tabular export formats) but **must** preserve validation, evidence grounding, and strict API contracts.

---

## 9. Implementation Lifecycle & Checkpoints
- **Gate Review Requirement**: **No implementation phase may proceed without formal checkpoint approval**. Developers must pass all validation tests, satisfy the definition of done, and obtain team review sign-off before code merging or transitioning to the next phase.
