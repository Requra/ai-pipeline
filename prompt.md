
````md
You are a careful implementation agent working on Requra.AI.

Repository:
`Requra/ai-pipeline`

Branch:
`review/full-pipeline-merge`

Your task:
Start **Phase 1 only** from the production-readiness documentation.

You are NOT allowed to work on Phase 2 or later.
You are NOT allowed to redesign the graph.
You are NOT allowed to modify AI nodes except where Phase 1 explicitly requires dependency/startup safety.
You must follow the markdown docs line-by-line.

---

# Main rule

Read the docs first. Then implement only what Phase 1 requires.

Read these files before changing code:

```txt
rules.md
docs/production-readiness/FINAL_DOCS_SIGNOFF.md
docs/production-readiness/IMPLEMENTATION_PLAN.md
docs/production-readiness/PHASE_CHECKPOINTS.md
docs/production-readiness/API_CONTRACT_TARGET.md
docs/production-readiness/FINAL_TARGET_ARCHITECTURE.md
docs/production-readiness/VALIDATION_STRATEGY.md
docs/production-readiness/OBSERVABILITY_STRATEGY.md
docs/production-readiness/RISK_REGISTER.md
````

Then focus only on:

```txt
Phase 1 — Production Foundation and Dependency Safety
```

---

# Phase 1 goal

Implement production foundation and dependency safety only.

Phase 1 must cover:

1. Add missing Python runtime dependencies.
2. Add missing system dependencies like `ffmpeg`.
3. Add startup/provider environment validation.
4. Ensure the app fails safely with clear messages if required dependencies or environment keys are missing.
5. Validate that the app installs, compiles, builds, and starts safely.

---

# Allowed files to modify

You may modify only these files unless the Phase 1 docs explicitly require another file:

```txt
ai-service/pyproject.toml
ai-service/poetry.lock
ai-service/Dockerfile
ai-service/app/main.py
ai-service/app/llm.py
ai-service/app/config.py        # create only if needed
ai-service/app/startup.py       # create only if needed
ai-service/app/utils/env.py     # create only if needed
docs/production-readiness/PHASE_CHECKPOINTS.md
docs/production-readiness/FINAL_DOCS_SIGNOFF.md
```

If you believe another file must change, stop and explain why before editing.

---

# Forbidden actions

Do NOT modify:

```txt
ai-service/app/graph/pipeline.py
ai-service/app/graph/router.py
ai-service/app/nodes/ingest.py
ai-service/app/nodes/transcribe.py
ai-service/app/nodes/extract.py
ai-service/app/nodes/classify.py
ai-service/app/nodes/generate.py
ai-service/app/nodes/summarize.py
ai-service/app/nodes/format.py
ai-service/app/schemas/items.py
ai-service/app/schemas/pipeline_state.py
```

Do NOT implement:

```txt
detect_file_type_node
parse_to_chunks_node
relevance_check_node
evidence_grounding_node
quality_gate_node
repair_node
export_formatter_node
contract_formatter_node
```

Those are later phases.

---

# Dependencies to verify/add

Inspect imports in the current code.

Known likely missing Python packages:

```txt
pymupdf
python-docx
groq
httpx
pydub
openai
```

Do not blindly add packages. First verify they are used by the code.

If used, ensure they are declared in `pyproject.toml`.

Also verify that system dependency `ffmpeg` is installed in the Docker image because transcription compression/chunking uses ffmpeg through subprocess.

---

# Environment variables

Add safe validation for required/optional environment variables.

Known provider-related variables:

```txt
GOOGLE_API_KEY
GROQ_API_KEY
DEEPGRAM_API_KEY
TRANSCRIBE_PROVIDER
GROQ_WHISPER_MODEL
GROQ_LANGUAGE
```

Rules:

1. The app must not crash with unclear import/provider errors.
2. Missing required keys must produce clear startup logs or controlled errors.
3. Do not require every provider key if the provider is not active.
4. If `TRANSCRIBE_PROVIDER=groq`, validate `GROQ_API_KEY`.
5. If `TRANSCRIBE_PROVIDER=deepgram`, validate `DEEPGRAM_API_KEY`.
6. Validate `GOOGLE_API_KEY` because the LLM uses Gemini by default.
7. Use safe defaults only if they are documented.
8. Do not print secrets.

---

# Implementation steps

Follow this exact sequence.

## Step 1 — Inspect Phase 1 docs

Read:

```txt
docs/production-readiness/IMPLEMENTATION_PLAN.md
docs/production-readiness/PHASE_CHECKPOINTS.md
rules.md
```

Find the Phase 1 section and summarize it before coding.

Output:

```md
## Phase 1 Scope Summary
- Files allowed:
- Changes planned:
- Validation commands:
- Risks:
```

Do not edit until this summary is printed.

---

## Step 2 — Audit dependencies

Inspect:

```txt
ai-service/pyproject.toml
ai-service/Dockerfile
ai-service/app/main.py
ai-service/app/llm.py
ai-service/app/nodes/ingest.py
ai-service/app/nodes/transcribe.py
```

Create a dependency audit table:

```md
| Dependency | Used In | Current Status | Action |
|---|---|---|---|
```

Then update only dependency-related files.

---

## Step 3 — Update Python dependencies

Update `pyproject.toml`.

Add only packages that are actually imported or required by Phase 1.

Likely candidates:

```toml
pymupdf = "..."
python-docx = "..."
groq = "..."
httpx = "..."
pydub = "..."
openai = "..."
```

Use stable compatible versions.
Do not upgrade unrelated packages unless necessary.

Then update lock file using the project’s normal Poetry command.

---

## Step 4 — Update Dockerfile

Add `ffmpeg` installation.

Also add any required OS libraries for document/audio processing if needed.

Keep the Dockerfile minimal and production-safe.

Do not install unnecessary large packages.

---

## Step 5 — Add startup validation

Implement a small startup validation helper.

Preferred design:

```txt
ai-service/app/startup.py
```

or:

```txt
ai-service/app/config.py
```

It should validate:

* required Python packages can import
* `ffmpeg` exists on PATH
* active provider API keys exist
* Gemini key exists for default LLM use

It should return clear errors.

It must not expose secret values.

Integrate it into FastAPI startup in `main.py`.

Use FastAPI lifespan/startup pattern if suitable.

---

# Important design rule for validation

Do not make local development impossible.

Use this behavior:

```txt
ENV=production
    strict validation enabled

ENV=development or missing
    warn clearly but allow boot where safe
```

However, if a feature is called and its provider key is missing, it must fail with a clear controlled error.

Document this behavior in comments and report.

---

# Validation commands

Run these commands from `ai-service` where appropriate:

```bash
poetry lock
poetry install
python -m compileall app
poetry run python -m compileall app
poetry run pytest
```

Then from repo root, run:

```bash
docker compose build ai-service
```

If `docker compose build ai-service` fails because the compose service name differs, inspect `docker-compose.yml` and run the correct command.

If tests fail for unrelated existing reasons, do not hide it. Report clearly:

```txt
FAILED: existing test failure / dependency issue / new regression
```

---

# Completion rules

Phase 1 is complete only if:

1. All imported third-party packages are declared.
2. Docker image includes ffmpeg.
3. Startup validation exists.
4. Missing env vars produce clear messages.
5. No node behavior was redesigned.
6. No graph changes were made.
7. Baseline Python code compiles.
8. Docker build either passes or failure is clearly explained.
9. Phase 1 checkpoint docs are updated with actual result.
10. Final report is produced.

---

# Output report

At the end, print:

```md
# Phase 1 Implementation Report

## Files Changed
...

## Dependency Changes
| Dependency | Why Added | File/Node That Uses It |
|---|---|---|

## Docker Changes
...

## Startup Validation Added
...

## Commands Run
| Command | Result |
|---|---|

## Bugs Found
...

## Phase 1 Acceptance Criteria
- [ ] Dependencies declared
- [ ] ffmpeg installed in Docker
- [ ] env validation added
- [ ] compile passes
- [ ] tests pass or failures explained
- [ ] docker build passes or failures explained
- [ ] no graph/node redesign happened

## Remaining Risks
...

## GO/NO-GO for Phase 2
Do not say GO unless Phase 1 validation passed.
```

---

# Behavior expectations

Be conservative.
Do not be creative.
Do not jump phases.
Do not implement future architecture.
Do not touch unrelated files.
If confused, stop and ask.

```
```
