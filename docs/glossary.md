# Glossary

Purpose: Define Requra.AI terms as used in this codebase. Audience: All contributors.

| Term | Meaning in this codebase |
|---|---|
| AI job | Durable processing record identified by `job_id`; it owns one or more attempts and a final result. |
| Attempt | One execution of a job. `/retry` increments the attempt number while preserving the logical job ID. |
| BM25 | Local lexical ranking used by `LexicalRetriever` to find source chunks containing relevant terms. |
| Chunk | A bounded `SourceChunk` carrying text and source offsets/page/speaker/time metadata. |
| Evidence | A quote and chunk reference attached to a requirement or story to support traceability. |
| Grounding | Verifying that generated/extracted evidence actually occurs verbatim in source chunks. |
| Hybrid retrieval | Optional merge of BM25 lexical results with PostgreSQL/pgvector vector results. |
| LangGraph | The graph runtime used to execute the 13-node `StateGraph` over `PipelineState`. |
| LLM | Chat model used for relevance, extraction, classification, conflict detection, story generation, repair, summary, and regeneration. |
| PipelineState | Typed shared dictionary passed between graph nodes; it contains intermediate state not all exposed publicly. |
| RAG | Source retrieval for evidence grounding; it is not a standalone conversational chatbot. |
| RQ | Redis Queue, used by the worker process when `REDIS_URL` is configured. |
| Source document | Backend-owned source reference and metadata stored by the AI service; raw long-term file ownership remains external. |
| Source preparation | The `prepare_sources` node encapsulating concurrent doc extraction, speech-to-text, PII masking, and chunking into a unified corpus. |
| Quality gate | Node that checks requirement/story/coverage quality and computes aggregate `quality_report`. |
| Quality repair | Optional bounded loop that asks an LLM to repair selected story issues before summary/format. |
| V1 result | Public `JobResult` model produced by `format_node`; contract version defaults to `1.0`. |
| FR/NFR/BR | Functional, non-functional, and business requirement labels used by classification and V1 mapping. |
| STT | Speech-to-text transcription, implemented through Groq Whisper or Deepgram in `audio.py`. |
| Tenant/project scope | Identifiers used to keep durable job, chunk, embedding, and retrieval data isolated. |
