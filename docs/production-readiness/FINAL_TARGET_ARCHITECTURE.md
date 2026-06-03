# 🧬 Final Target Architecture (Requra.AI Pipeline)

This document describes the redesigned LangGraph structure, defining how the 15 nodes interact, route, and maintain state safety.

---

## 1. The Redesigned Pipeline Flow

The execution flow has been split into highly specialized, single-responsibility nodes. The conditional routing has been hardened to prevent state leakage and ensure execution predictability.

```mermaid
graph TD
    START([START]) --> detect_file_type[detect_file_type]
    detect_file_type --> ingest[ingest]
    ingest --> route_audio{Is Audio?}
    
    route_audio -- YES --> transcribe_if_audio[transcribe_if_audio]
    route_audio -- NO --> parse_to_chunks[parse_to_chunks]
    
    transcribe_if_audio --> parse_to_chunks
    parse_to_chunks --> relevance_check[relevance_check]
    
    relevance_check --> route_relevance{Is Relevant?}
    route_relevance -- YES --> extract_requirements[extract_requirements]
    route_relevance -- NO --> contract_formatter[contract_formatter]
    
    extract_requirements --> classify_requirements[classify_requirements]
    classify_requirements --> deduplicate_requirements[deduplicate_requirements]
    deduplicate_requirements --> evidence_grounding[evidence_grounding]
    evidence_grounding --> generate_user_stories[generate_user_stories]
    generate_user_stories --> quality_gate[quality_gate]
    
    quality_gate --> route_quality{Quality Issues?}
    route_quality -- YES --> repair_if_needed[repair_if_needed]
    route_quality -- NO --> summarize_structured[summarize_structured]
    
    repair_if_needed --> quality_gate_pass_2[quality_gate (Pass 2)]
    quality_gate_pass_2 --> route_quality_pass_2{Quality Issues?}
    route_quality_pass_2 -- YES/NO --> summarize_structured
    
    summarize_structured --> export_formatter[export_formatter]
    export_formatter --> contract_formatter[contract_formatter]
    contract_formatter --> END([END])

    classDef newNode fill:#4CAF50,stroke:#388E3C,color:#fff;
    classDef modNode fill:#FF9800,stroke:#F57C00,color:#fff;
    classDef origNode fill:#2196F3,stroke:#1976D2,color:#fff;
    
    class detect_file_type,parse_to_chunks,relevance_check,deduplicate_requirements,evidence_grounding,quality_gate,repair_if_needed,contract_formatter,export_formatter newNode;
    class ingest,transcribe_if_audio,extract_requirements,classify_requirements,generate_user_stories,summarize_structured modNode;
```

---

## 2. Redesigned Node Sequence

The pipeline now utilizes the following 15-node execution sequence:

1. **`detect_file_type` (New)**: Decoupled from Ingest. Inspects file stream signatures using MIME logic.
2. **`ingest` (Modified)**: Stripped of parsing, chunking, and relevance checking. Focused strictly on loading file bytes and PII masking.
3. **`transcribe_if_audio` (Modified)**: Refactored to return structured transcription chunks (`SourceChunk`) with speaker IDs and time segments.
4. **`parse_to_chunks` (New)**: Standardizes document parsing into token/page-aware chunks, keeping coordinate metadata.
5. **`relevance_check` (New)**: Evaluates multiple chunks across the document. Skips downstream extraction for irrelevant files, routing to the final contract formatter with a rejected status.
6. **`extract_requirements` (Modified)**: Extracts Functional, Non-Functional, Business Rules, Constraints, Assumptions, Open Questions, and Out-of-Scope items concurrently across chunks. Requires grounding evidence and outputs **`extracted_requirements`**.
7. **`classify_requirements` (Modified)**: Inputs `extracted_requirements`, verifies category assignments and confidence thresholds, and outputs **`classified_requirements`**.
8. **`deduplicate_requirements` (New)**: Merges identical requirements, preserves all unique labels, and aggregates evidence quotes.
9. **`evidence_grounding` (New)**: Verifies all requirements have non-empty source quotes (`len(evidence) >= 1`) matching original chunk texts.
10. **`generate_user_stories` (Modified)**: Translates requirements into real `UserStory` items only. Populates `RequirementCoverage` models tracking card mappings (one-to-one, one-to-many, many-to-one, attached-as-acceptance-criteria, non-story, needs_review).
11. **`quality_gate` (New)**: Validates requirements, stories, coverage records, and acceptance criteria. Routes to `repair_if_needed` on issues.
12. **`repair_if_needed` (New)**: Attempts self-repair (maximum 2 attempts). Loop path goes: `quality_gate` -> `repair_if_needed` -> `quality_gate`. If issues remain after attempt limit, flags items as `needs_review` and routes to summary.
13. **`summarize_structured` (Modified)**: Outputs a Pydantic `StructuredSummary` containing 9 mandatory sections.
14. **`export_formatter` (New)**: Standardizes CSV/Excel/Jira rows, running *before* the final response formatting node.
15. **`contract_formatter` (New)**: The final node in the graph. Assembles and validates the strict Pydantic `JobResult` client contract.

---

## 3. Deprecated Behaviors
- **Card Cardinality Limits**: The 1:1 user story mapping is deprecated in favor of the flexible `RequirementCoverage` model.
- **Frontend File Trust**: The pipeline no longer trusts client-supplied parameters; `detect_file_type` determines the document parser.
- **Naïve Equal-Word Chunking**: Splitting files into exactly 5 equal-word chunks is deprecated in favor of token-size/page boundary splitting.
- **Hallucinated Mock Fallbacks**: Hardcoded static mock lists on failures are replaced with typed partial returns and `needs_review` status updates.
- **Untyped Response Outputs**: Direct execution state returns are replaced by the strictly-validated Pydantic `JobResult` contract formatter.
