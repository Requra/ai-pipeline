Phase 5 is **NO-GO for Phase 6** right now.

The blockers are real:

```txt id="p5-blockers"
1. preprocess_text can damage technical terms like ER diagram / AH header
2. EvidenceSpan.quote may come from cleaned text, not exact original chunk text
```

Do **not** move to Phase 6 until this is fixed.

Use this compact blocker-fix prompt:

````md id="phase5-blocker-fix"
You are fixing Phase 5 blockers only.

Repo: `Requra/ai-pipeline`
Branch: `review/full-pipeline-merge`

Do NOT start Phase 6.
Do NOT modify classify/generate/summarize/format.
Do NOT implement deduplication.
Do NOT implement evidence_grounding node.

Read:
- `ai-service/app/nodes/extract.py`
- `ai-service/tests/nodes/test_extract.py`
- `docs/production-readiness/nodes/06_extract_node.md`
- `docs/production-readiness/PHASE_CHECKPOINTS.md`
- `docs/production-readiness/IMPLEMENTATION_PLAN.md`

Fix only these blockers:

## 1. Preprocessing acronym bug
Current issue:
`preprocess_text` removes fillers case-insensitively, so technical acronyms like `ER` and `AH` can be damaged.

Required:
- Preserve uppercase acronyms like `ER`, `AH`.
- Do not remove meaningful technical terms.
- Make filler removal safe and conservative.
- Add tests:
  - `"ER diagram"` remains `"ER diagram"`
  - `"AH header"` remains `"AH header"`
  - lowercase filler words like `"um"` / `"uh"` can still be cleaned when safe

## 2. Evidence quote must align with original source chunk
Current issue:
Evidence fallback may use `clean_text[:200]`, which may not exist exactly in `chunk.text`.

Required:
- `EvidenceSpan.quote` must come from the original `SourceChunk.text`, not cleaned/preprocessed text.
- If LLM returns an evidence quote that exists in original chunk, keep it.
- If LLM evidence quote does not exist exactly, try a safe normalized match against original chunk text.
- If no safe match exists, use a real source substring from original chunk as fallback and mark `needs_review=True` with clear `review_reason`.
- Never use cleaned text as final evidence quote unless it maps back to original source text.
- Add tests proving every `EvidenceSpan.quote` is found in original `chunk.text`.

Allowed files:
- `ai-service/app/nodes/extract.py`
- `ai-service/tests/nodes/test_extract.py`
- Phase 5 docs/checkpoints only

Run:
```bash
cd ai-service
python -m compileall app
poetry run python -m compileall app
poetry run pytest tests/nodes/test_extract.py -vv
poetry run pytest
````

Final report:

```md
# Phase 5 Blocker Fix Report

## Files Changed
...

## Fix 1 — Preprocessing Acronym Safety
Before:
After:
Tests:

## Fix 2 — Evidence Quote Source Alignment
Before:
After:
Tests:

## Commands Run
| Command | Result | Notes |
|---|---|---|

## Remaining Issues
...

## Final GO/NO-GO for Phase 6
GO/NO-GO
```

```

Important: do **not** push the quote-alignment problem to Phase 8 only. Phase 8 can verify grounding later, but Phase 5 must already produce evidence quotes that are source-aligned.
```
