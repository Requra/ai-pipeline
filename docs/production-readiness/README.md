# 🚀 Requra.AI Production Readiness Documentation

Welcome to the Requra.AI Production Readiness Documentation suite. This directory contains the complete blueprint, audits, strategy plans, and node contracts required to migrate the Requra AI Pipeline from its current functional baseline to a production-ready, enterprise-grade MVP.

---

## 🗺️ Documentation Directory Map

### 📋 Planning & Strategy Docs
* **[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)**: A detailed 11-phase roadmap covering foundational fixes, state refactoring, individual node upgrades, quality gates, structured summaries, and testing.
* **[PHASE_CHECKPOINTS.md](PHASE_CHECKPOINTS.md)**: Detailed criteria for validating every phase of migration, including rollback instructions and definition of done metrics.
* **[PRODUCTION_GAPS_AUDIT.md](PRODUCTION_GAPS_AUDIT.md)**: An in-depth audit of the current pipeline highlighting security, stability, parsing, and quality gaps.
* **[FINAL_TARGET_ARCHITECTURE.md](FINAL_TARGET_ARCHITECTURE.md)**: Details the redesigned 14-node graph structure, state routing, deprecations, and node relationships.
* **[VALIDATION_STRATEGY.md](VALIDATION_STRATEGY.md)**: Testing strategy covering unit, integration, contract, golden dataset evaluation, and system package testing.
* **[OBSERVABILITY_STRATEGY.md](OBSERVABILITY_STRATEGY.md)**: Technical design for structured logging, token tracking, quality metric emission, and trace propagation.
* **[API_CONTRACT_TARGET.md](API_CONTRACT_TARGET.md)**: Defines the production Pydantic state schemas, input models, and API responses.
* **[RISK_REGISTER.md](RISK_REGISTER.md)**: Catalog of security, reliability, model, and integration risks along with mitigation actions.
* **[DOCS_REVIEW_REPORT.md](DOCS_REVIEW_REPORT.md)**: Assessment of documentation compliance and required adjustments.

### 🧩 Node-Level Deep Dives (`nodes/`)
1. **[01 Ingest Node](nodes/01_ingest_node.md)**: Standardizes extraction inputs, masks PII, and handles basic validation.
2. **[02 Transcribe Node](nodes/02_transcribe_node.md)**: HARDENED: Preserves speaker diarization and timestamps for downstream citation.
3. **[03 Detect File Type Node [NEW]](nodes/03_detect_file_type_node.md)**: MIME-based file type detection replacing frontend parameters.
4. **[04 Parse to Chunks Node [NEW]](nodes/04_parse_to_chunks_node.md)**: Page-aware and paragraph-aware source document segmenter.
5. **[05 Extract Node [MODIFIED]](nodes/05_extract_node.md)**: Expanded to extract FRs, NFRs, BRs, constraints, assumptions, open questions, and out-of-scope items.
6. **[06 Classify Node [MODIFIED]](nodes/06_classify_node.md)**: Multi-label classification with strict thresholds.
7. **[07 Evidence Grounding Node [NEW]](nodes/07_evidence_grounding_node.md)**: Proves requirements correspond to source sentences using mathematical overlap/LLM assertion.
8. **[08 Deduplicate Node [NEW]](nodes/08_deduplicate_node.md)**: Merges identical requirements and accumulates source citations.
9. **[09 Generate Node [MODIFIED]](nodes/09_generate_node.md)**: Upgraded user story mapper supporting complex requirement cardinality mappings.
10. **[10 Quality Gate Node [NEW]](nodes/10_quality_gate_node.md)**: Validates requirements and stories against quality and alignment metrics.
11. **[11 Repair Node [NEW]](nodes/11_repair_node.md)**: Corrects quality issues using graph loops and target prompts.
12. **[12 Summarize Node [MODIFIED]](nodes/12_summarize_node.md)**: Redesigned structure covering Executive Summary, Risks, Decisions, and Open Questions.
13. **[13 Contract Formatter Node [NEW]](nodes/13_contract_formatter_node.md)**: Production response builder aligning state with API contract output.
14. **[14 Export Formatter Node [NEW]](nodes/14_export_formatter_node.md)**: Generates CSV, Excel, and Jira-compatible export tables.

---

## 🗺️ Final Target Architecture Graph

```txt
                       [START]
                          │
               ┌──────────▼──────────┐
               │   detect_file_type  │
               └──────────┬──────────┘
                          │
                  ┌───────▼───────┐
                  │    ingest     │
                  └───────┬───────┘
                          │
                (Is it an audio file?)
                     /          \
                  YES            NO
                   /              \
         ┌────────▼────────┐   ┌───▼──────────────┐
         │  transcribe_    │   │  parse_to_       │
         │  if_audio       │   │  chunks          │
         └────────┬────────┘   └───┬──────────────┘
                  │                │
                  └───────┬────────┘
                          │
                  ┌───────▼───────┐
                  │  relevance_   │
                  │  check        │
                  └───────┬───────┘
                          │
               (Is the document relevant?)
                     /          \
                  YES            NO ──────────────┐
                   /                              │
         ┌────────▼────────┐                      │
         │  extract_       │                      │
         │  requirements   │                      │
         └────────┬────────┘                      │
                  │                               │
         ┌────────▼────────┐                      │
         │  classify_      │                      │
         │  requirements   │                      │
         └────────┬────────┘                      │
                  │                               │
         ┌────────▼────────┐                      │
         │  deduplicate_   │                      │
         │  requirements   │                      │
         └────────┬────────┘                      │
                  │                               │
         ┌────────▼────────┐                      │
         │    evidence_    │                      │
         │    grounding    │                      │
         └────────┬────────┘                      │
                  │                               │
         ┌────────▼────────┐                      │
         │  generate_      │                      │
         │  user_stories   │                      │
         └────────┬────────┘                      │
                  │                               │
         ┌────────▼────────┐                      │
         │  quality_gate   │                      │
         └────────┬────────┘                      │
                  │                               │
            (Issues found?)                       │
               /        \                         │
            YES          NO                       │
             /            \                       │
     ┌──────▼──────┐   ┌───▼──────────────┐       │
     │  repair_    │   │  summarize_      │       │
     │  if_needed  │   │  structured      │       │
     └──────┬──────┘   └───┬──────────────┘       │
            │              │                      │
            └──────┬───────┘                      │
                   │                              │
         ┌─────────▼────────┐                     │
         │ contract_        ◄─────────────────────┘
         │ formatter        │
         └─────────┬────────┘
                   │
         ┌─────────▼────────┐
         │ export_          │
         │ formatter        │
         └─────────┬────────┘
                   │
                [END]
```

---

## 🛠️ Validation Pipeline

To check compilation and run the test suite across the repository, execute:
```bash
# Install dependencies
cd ai-service
poetry install

# Validate code compilation
python -m compileall app

# Run full test suite
poetry run pytest
```
*Note: Refer to [VALIDATION_STRATEGY.md](VALIDATION_STRATEGY.md) for deeper verification protocols.*
