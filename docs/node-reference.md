# Node Reference

The pipeline is a LangGraph `StateGraph` over `PipelineState`. Final flow:

```
detect_file_type
→ ingest
→ (audio?) transcribe
→ parse_to_chunks
→ build_source_index
→ extract
→ dedupe_requirements
→ retrieve_evidence
→ classify
→ evidence_grounding
→ generate
→ quality_gate
→ summarize
→ format → END
```

Routing: after `ingest`, a conditional router sends audio to `transcribe`,
rejected/irrelevant input straight to `format`, and everything else to
`parse_to_chunks`.

> The compiled graph is bound with `recursion_limit=60` (`.with_config`) because
> this pipeline is a long linear chain and langgraph 0.0.26 spends extra
> channel-propagation super-steps per node. The graph is acyclic; this is only a
> step budget.

| # | Node | Responsibility | Key outputs |
|---|------|----------------|-------------|
| 1 | `detect_file_type` | Determine input type (pdf/docx/text/audio). | `file_type` |
| 2 | `ingest` | Extract text, normalize, mask light PII, LLM relevance gate. | `raw_text`, `is_useful`, `relevance_score` |
| 3 | `transcribe` | Audio → text (Groq/Deepgram). Audio path only. | `raw_text`/`chunks` |
| 4 | `parse_to_chunks` | Split into coordinate-aware `SourceChunk`s. | `chunks` |
| 5 | `build_source_index` | Build the in-memory BM25 index for the job. | `source_index_id`, `retrieval_stats` |
| 6 | `extract` | Grounded requirement extraction (verbatim quotes, JSON repair, confidence grading). | `extracted_requirements` |
| 7 | `dedupe_requirements` | Merge exact/near-duplicate requirements; preserve evidence; re-id. | `extracted_requirements` (deduped) |
| 8 | `retrieve_evidence` | Attach best supporting evidence per requirement; record retrieval scores. | `extracted_requirements` (+evidence/scores) |
| 9 | `classify` | Multi-label classify (FR/NFR/BR + special). | `classified_requirements` |
| 10 | `evidence_grounding` | Validate every requirement has grounded evidence. | `quality_issues` |
| 11 | `generate` | Requirements → user stories with ≥2 specific GWT acceptance criteria; coverage map; validation. | `user_stories`, `requirement_coverages` |
| 12 | `quality_gate` | Derive quality scores; emit meaningful issues; set review status. | `quality_issues`, `quality_report` |
| 13 | `summarize` | Structured executive summary grounded in raw text + artifact digest. | `summary` |
| 14 | `format` | Assemble the public `JobResult` (V1 contract): requirements, stories, coverages, summary, exports, quality report, status. | `job_result` |

## Status mapping (in `format`)

- `rejected` — input judged not useful (takes precedence; rejection is not a failure).
- `failed` — a system error with no usable output.
- `partial` — usable output but warnings or high-severity issues exist.
- `completed` — requirements + stories produced with no warnings/high-severity issues.

## Warning codes (selected)

`SOURCE_INDEX_EMPTY`, `EXTRACT_EMPTY`, `EXTRACT_WEAK_EVIDENCE`,
`DUPLICATE_REQUIREMENT_MERGED`, `POSSIBLE_DUPLICATE_REVIEW`, `WEAK_EVIDENCE_SUPPORT`,
`NO_RETRIEVED_EVIDENCE`, `EVIDENCE_LIMIT_APPLIED`, `GENERATE_SKIPPED_NO_REQUIREMENTS`,
`GENERATE_STORY_QUALITY`, `GENERATE_LLM_FAILURE_FALLBACK`.
