You are continuing Requra/ai-pipeline on branch review/full-pipeline-merge.

Goal:
Make LangSmith Studio / LangGraph Studio work correctly for local graph debugging.

Current issue:
Studio shows:

Failed to initialize Studio
TypeError: Failed to fetch
Please verify if the API server is running or accessible from the browser.

Important:
The current FastAPI server runs with:
uvicorn app.main:app --reload

But LangSmith Studio does NOT connect to the FastAPI `/process-json` server.
Studio needs a LangGraph Agent Server started by:

langgraph dev

LangGraph docs show:
- `langgraph dev` is the lightweight local development server.
- Default API port is 2024.
- Studio URL should point to baseUrl=http://127.0.0.1:2024 or http://localhost:2024.

Do NOT:
- Change the production FastAPI endpoint.
- Change pipeline graph order.
- Add new pipeline nodes.
- Break `/process-json`.
- Add Docker-only setup for this task.
- Require `langgraph up` for normal local debugging.

Tasks:

1. Add/verify `ai-service/langgraph.json`

Create or fix:

{
  "dependencies": ["."],
  "graphs": {
    "requra_pipeline": "./app/graph/pipeline.py:graph"
  },
  "env": "./.env"
}

Make sure `app/graph/pipeline.py` exports a compiled graph variable named:

graph

Example:

graph = build_pipeline()

LangGraph docs show `langgraph.json` maps graph names to Python file paths and graph variables, and can load env from `.env`.

2. Install LangGraph CLI dev dependency

Add if missing:

poetry add --group dev "langgraph-cli[inmem]"

or ensure it exists in pyproject dev dependencies.

3. Add a local Studio startup command

Add docs or script:

cd ai-service
poetry run langgraph dev

Expected CLI output should include:
- API: http://localhost:2024
- Docs: http://localhost:2024/docs
- Studio Web UI: https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024

4. Add a simple health check command

After `langgraph dev` starts, verify from browser or terminal:

curl http://127.0.0.1:2024/docs

Also test:

curl http://127.0.0.1:2024/ok

If `/ok` is not available, at minimum `/docs` should load.

5. Fix Studio connection instructions

In LangSmith Studio:
- Click Server connection settings.
- Set API server/base URL to:

http://127.0.0.1:2024

If that fails, try:

http://localhost:2024

Do not set it to:
http://127.0.0.1:8000

because port 8000 is FastAPI, not LangGraph Agent Server.

6. Add Studio-friendly test input documentation

Because this graph expects PipelineState, not a normal chat message, add docs showing a valid input payload.

Create docs/testing/LANGGRAPH_STUDIO.md with:

- How to run `langgraph dev`
- What URL to use in Studio
- Difference between FastAPI testing and Studio testing
- Example Studio input state

Example Studio input:

{
  "job_id": "studio-test-001",
  "raw_bytes": "",
  "raw_text": "The system shall allow users to register using email and password. Admins shall export customer reports as CSV and PDF. The dashboard must load in less than 2 seconds.",
  "file_type": "text",
  "metadata": {
    "source": "studio_manual_test"
  },
  "source_metadata": null,
  "chunks": [],
  "extracted_requirements": [],
  "classified_requirements": [],
  "requirement_coverages": [],
  "user_stories": [],
  "quality_issues": [],
  "warnings": [],
  "export_rows": [],
  "summary": null,
  "job_result": null,
  "is_useful": false,
  "relevance_score": 0,
  "status": "started",
  "error": null,
  "started_at": 0,
  "processing_time_ms": 0,
  "functional_requirements": []
}

7. Check raw_bytes issue

If Studio fails because `raw_bytes` expects bytes and JSON provides string:
- Do not break FastAPI.
- Add a Studio/dev-only wrapper graph OR make detect/ingest tolerate `raw_bytes=""`.
- Prefer minimal safe fix:
  - If raw_bytes is a string, convert it to bytes internally only where needed.
  - Or allow raw_text-only state for Studio testing.

8. Add docs note

Clarify:

FastAPI testing:
poetry run uvicorn app.main:app --reload
POST http://127.0.0.1:8000/process-json

Studio testing:
poetry run langgraph dev
Open Studio URL with baseUrl=http://127.0.0.1:2024

They are different servers.

9. Run validation

Run:

cd ai-service
poetry run pytest -q

Then run:

poetry run langgraph dev

Verify:
- terminal shows API URL on port 2024
- http://127.0.0.1:2024/docs opens
- Studio opens without Failed to fetch
- requra_pipeline appears as an assistant/graph
- one Studio run can execute with sample state
- LangSmith traces appear under project requra-ai-pipeline-mvp

10. Final report

Return:
- Files changed
- Exact command to start Studio
- Correct Studio baseUrl
- Whether graph loaded
- Whether sample run worked
- Any remaining Studio limitations