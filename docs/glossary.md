# Glossary

Purpose: define Requra.AI terms in the way this repository uses them. Audience: all contributors.

| Term | Meaning in this codebase |
|---|---|
| AI job | Durable processing record identified by `job_id`; it owns one or more attempts and a final result. |
| Attempt | One execution of a job. `/retry` increments the attempt number while preserving the logical job id. |
| BM25 | Local lexical ranking used by `LexicalRetriever` to find source chunks containing relevant terms. |
| Chunk | A bounded `SourceChunk` carrying text and source offsets/page/speaker/time metadata. |
| Evidence | A quote and chunk reference attached to a requirement or story to support traceability. |
| Grounding | Verifying that generated/extracted evidence actually occurs in source chunks. |
| Hybrid retrieval | Optional merge of BM25 results with PostgreSQL/pgvector vector results. |
| LangGraph | The graph runtime used to execute the 15-node `StateGraph` over `PipelineState`. |
| LLM | Chat model used for relevance, extraction, classification, conflict detection, story generation, repair, summary, and regeneration. |
| PipelineState | Typed shared dictionary passed between graph nodes; it contains internal data not all exposed publicly. |
| RAG | Here, source retrieval for evidence grounding; it is not a separate conversational chatbot. |
| RQ | Redis Queue, used by the separate worker mode when `REDIS_URL` is configured. |
| Source document | Backend-owned source reference and metadata stored by the AI service; raw long-term file ownership remains external. |
| Quality gate | Node that checks requirement/story/coverage quality and computes aggregate `quality_report`. |
| Quality repair | Optional bounded loop that asks an LLM to repair selected story issues before summary/format. |
| V1 result | Public `JobResult` model produced by `format_node`; contract version defaults to `1.0`. |
| FR/NFR/BR | Functional, non-functional, and business requirement labels used by classification and V1 mapping. |
| STT | Speech-to-text transcription, implemented through Groq or Deepgram in `transcribe.py`. |
| Tenant/project scope | Identifiers used to keep durable job, chunk, embedding, and retrieval data separated. |
