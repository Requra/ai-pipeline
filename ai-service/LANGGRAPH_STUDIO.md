# 🧪 Running with LangGraph Studio

This guide explains how to visually debug and execute the AI Pipeline using **LangGraph Studio**.

## 1. Prerequisites

- **LangGraph Studio App**: Ensure you have the [LangGraph Studio](https://github.com/langchain-ai/langgraph-studio) application installed.
- **Docker**: LangGraph Studio usually runs within a Docker container to manage its own environment. Make sure Docker Desktop is running.
- **API Keys**: Ensure your `.env` file in the `ai-service` directory has a valid `GOOGLE_API_KEY`.

## 2. Launching the Studio

1.  **Open LangGraph Studio.**
2.  **Select the Project**: Choose the `ai-service` directory (`c:\ITI_GP\src\ai-pipeline\ai-service`).
3.  **Wait for Build**: The studio will build the environment using the `langgraph.json` and `pyproject.toml` files.

> [!NOTE]
> If the environment fails to build, check that your `pyproject.toml` has all required dependencies and that Docker has enough resources.

## 3. Required JSON Input

Once the graph loads, you will see a **Threads** panel. Create a new thread and paste the following JSON into the **Input** section:

### Standard Simulation Input
Use this to bypass mock file extraction and test the full AI extraction logic.

```json
{
  "job_id": "studio-test-001",
  "raw_text": "The system shall allow users to browse products without logging in. Registered users MUST be able to add items to a shopping cart. The checkout process must support payments via Stripe and PayPal. The application must load and be interactive within 3 seconds for 95% of users. User passwords must be hashed using argon2 before storage in the database. All API requests must be logged for auditing purposes.",
  "file_type": "pdf"
}
```

### Mock File Extraction Input
Use this to test the initial `ingest` node's mock behavior.

```json
{
  "job_id": "mock-test",
  "file_type": "pdf"
}
```

## 4. Understanding the Fields

| Field | Type | Description |
| :--- | :--- | :--- |
| `job_id` | String | A unique identifier for the execution. |
| `raw_text` | String | **Required (min 50 chars)** if skipping file upload. The text the AI will analyze. |
| `file_type` | String | Must be `pdf`, `docx`, or `audio`. Tells the `ingest` node how to handle the input. |

## 5. Visual Debugging

- **Green Nodes**: Successfully completed.
- **Red Nodes**: Encountered an error (you can click them to see the `error` field in the state).
- **Inspect State**: Click any node in the graph after a run to see how the `PipelineState` (Functional Requirements, User Stories, etc.) was modified by that specific step.
