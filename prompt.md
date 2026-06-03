Phase 2 is approved, so the next prompt is **Phase 3 implementation**.

````md id="phase3-prompt"
You are implementing Phase 3 only for Requra.AI.

Repo: `Requra/ai-pipeline`
Branch: `review/full-pipeline-merge`

Phase:
`Phase 3 — File Type Detection and Source-Aware Parsing`

Do NOT start Phase 4.
Do NOT modify extraction, classification, generation, quality gate, repair, summary, or formatter logic.
Do NOT implement RAG.
Do NOT change schema contracts unless a tiny compatibility fix is required.

Read first:
- `rules.md`
- `docs/production-readiness/IMPLEMENTATION_PLAN.md`
- `docs/production-readiness/PHASE_CHECKPOINTS.md`
- `docs/production-readiness/API_CONTRACT_TARGET.md`
- `docs/production-readiness/nodes/03_detect_file_type_node.md`
- `docs/production-readiness/nodes/04_parse_to_chunks_node.md`
- `ai-service/app/graph/pipeline.py`
- `ai-service/app/graph/router.py`
- `ai-service/app/nodes/ingest.py`
- `ai-service/app/schemas/items.py`
- `ai-service/app/schemas/pipeline_state.py`

Goal:
Implement backend file type detection and source-aware parsing/chunking.

Allowed changes:
- `ai-service/app/nodes/detect_file_type.py` NEW
- `ai-service/app/nodes/parse_to_chunks.py` NEW
- `ai-service/app/nodes/ingest.py` minimal refactor only
- `ai-service/app/graph/pipeline.py`
- `ai-service/app/graph/router.py` only if needed
- `ai-service/app/schemas/*` only tiny compatibility fixes
- tests for Phase 3

Implement:
1. `detect_file_type_node`
   - inspect bytes, not frontend `file_type`
   - detect PDF, DOCX, text, audio
   - reject empty/unsupported/too-large files
   - output `file_type` and `DocumentSource`
   - do not trust client file_type

2. `parse_to_chunks_node`
   - PDF: page-aware chunks
   - DOCX: paragraph/table-aware chunks
   - text: token/paragraph-aware chunks
   - audio: do not transcribe yet; preserve route for Phase 4
   - output `List[SourceChunk]`
   - include `chunk_id`, text, char offsets, page/paragraph/time metadata when available
   - no exactly-5-equal-word splitting

3. Graph update
   - add `detect_file_type`
   - add `parse_to_chunks`
   - route document/text files through parse_to_chunks
   - keep audio transcription behavior compatible until Phase 4
   - do not break existing `/process`

Before editing, print:
```md
## Phase 3 Scope Summary
- Files to change:
- Nodes to add:
- Graph changes:
- Compatibility risks:
````

Run:

```bash
cd ai-service
poetry install --no-root
poetry run python -m compileall app
python -m compileall app
poetry run pytest
```

Final report:

```md
# Phase 3 Implementation Report

## Files Changed
...

## Nodes Added
...

## Graph Changes
...

## Parsing Behavior
...

## Backwards Compatibility
...

## Commands Run
| Command | Result | Notes |
|---|---|---|

## Issues Found
...

## Phase 3 Checklist
- [ ] Backend detects file type from bytes
- [ ] Unsupported files rejected safely
- [ ] PDF chunks preserve page metadata
- [ ] DOCX chunks preserve paragraph/table structure
- [ ] Text chunks avoid cutting words/sentences badly
- [ ] No naive 5 equal chunks
- [ ] Existing endpoints still compile
- [ ] No extraction/classification/generation changes
- [ ] Tests pass or failures explained

## Final GO/NO-GO for Phase 4
GO/NO-GO
```

```

One note: Phase 1 already reached GO after fixing OpenAI/default LLM validation and transcription provider validation, so Phase 3 can proceed after the Phase 2 approval you posted. :contentReference[oaicite:0]{index=0}
```
