# 🧪 Pipeline Validation & Testing Strategy

This document establishes the testing protocols, verification commands, and quality bars for validating the Requra.AI requirements extraction pipeline.

---

## 1. Validation Commands

The following commands must run successfully in each implementation phase:

```bash
# 1. Environment Setup & Dependency Installation
cd ai-service
poetry install

# 2. Code Compilation Verification
poetry run python -m compileall app

# 3. Unit and Integration Test Runner
poetry run pytest -v

# 4. Local Execution & Startup Verification
poetry run uvicorn app.main:app --host 0.0.0.0 --port 8000 --log-level info

# 5. Production Docker Image Builder
docker compose build ai-service

# 6. Container Integration Test
docker compose up ai-service
```

---

## 2. Test Architecture

To ensure the pipeline is stable and production-ready, we employ a 5-tier testing architecture:

### A. Unit Tests
- **Target**: Individual nodes (e.g. `detect_file_type`, `parse_to_chunks`, `classify_requirements`).
- **Mocking**: External APIs (OpenAI, Groq, Deepgram, Gemini) are mocked using standard fixtures in `tests/conftest.py`.
- **Validation**: Verifies state transitions, text normalization, chunk boundary calculations, and edge cases.

### B. Integration Tests
- **Target**: The complete LangGraph orchestrator graph (`app/graph/pipeline.py`).
- **Validation**: Traces routing behavior, state accumulation, conditional transitions, and short-circuiting logic.

### C. Golden Dataset Evaluation
- **Target**: Extraction accuracy and LLM formatting stability.
- **Methodology**: Run the pipeline against a static set of 10 human-curated requirement documents (PDFs, transcripts, Word documents) with known outputs.
- **Metrics**: 
  - Precision/Recall of extracted requirements.
  - Percentage of Given-When-Then matches.
  - Hallucination rate (flagged by grounding).
  - Grounding citation correctness.

### D. Contract Tests
- **Target**: API endpoints (`/process` and `/process-json`).
- **Validation**: Ensures that all success, partial, or error responses map strictly to the target Pydantic `JobResult` model schema without exposing graph internals.

### E. System Package and Docker boot Tests
- **Target**: Containerized file system support.
- **Validation**: Verifies `ffmpeg` and document converters are accessible inside the Docker environment by executing sample transcoder/converter calls.

---

## 3. Definition of Quality Gates

| Gate Metric | Threshold Requirement | How it is Evaluated |
| :--- | :--- | :--- |
| **Grounding Coverage** | 100% | Every requirement must contain a matching evidence quote from the source text. |
| **Agile Story Formatting** | 95%+ | Stories must follow the format `As a <actor>, I want <goal>, so that <benefit>`. |
| **Acceptance Criteria Validation** | 90%+ | Criteria must follow Given-When-Then patterns. |
| **Deduplication Rate** | 100% | Semantic deduplication must combine all overlapping requirement identifiers. |
| **API Code Health** | 100% | Zero syntax errors; all modules compile. |
