# 📜 AI Pipeline Data Contract

This document defines the input and output structure for the AI Requirement Extraction Pipeline. The contract ensures compatibility between the FastAPI interface and the underlying LangGraph execution.

## 📥 Input Contracts

### 1. Dev/Demo Endpoints (`/process`, `/process-json`)
These endpoints accept the basic `PipelineState` initialization object.

| Field | Type | Description |
| :--- | :--- | :--- |
| `job_id` | `string` | Unique identifier for the processing request. |
| `raw_text` | `string` | The text to be processed (extracted or transcribed). |
| `file_type` | `string` | `pdf`, `docx`, or `audio`. Determines the initial routing. |

### 2. Internal Production API (`POST /internal/jobs`)
For durable background execution, the internal API accepts:

| Field | Type | Description |
| :--- | :--- | :--- |
| `job_id` | `string` | Unique identifier (`^[A-Za-z0-9._-]{1,128}$`). |
| `tenant_id` | `string` | Tenant scope for project and data separation. |
| `project_id` | `string` | Project identifier. |
| `requested_by` | `string` | User ID initiating the request. |
| `input_type` | `string` | `text` | `backend_document` | `backend_transcript` | `backend_audio`. |
| `content` | `string` | Inline text content (required for `text` or `backend_transcript`). |
| `source_documents` | `array` | List of reference documents to parse (required for others). |
| `options` | `object` | Pipeline execution options (see below). |

**Production Options (`options`):**
* `generate_user_stories` (`boolean`): Enforce GWT Agile user stories generation.
* `generate_summary` (`boolean`): Create structured executive summaries.
* `enable_embeddings` (`boolean`): Generate semantic embeddings stored via pgvector.
* `enable_hybrid_retrieval` (`boolean`): Run hybrid BM25 + vector similarity search.
* `callback_url` (`string`): Webhook endpoint to notify backend of job updates.

### Example Input (Dev/Demo)
```json
{
  "job_id": "req-9921",
  "raw_text": "The system shall allow users to browse products without logging in. Registered users MUST be able to add items to a shopping cart. The checkout process must support payments via Stripe.",
  "file_type": "pdf"
}
```

---

## 📤 Output Contract (`JobResult`)

The API returns a structured `JobResult` object containing the categorized requirements and generated user stories.

### Example Output
```json
{
  "job_id": "req-9921",
  "status": "success",
  "user_stories": [
    {
      "title": "Guest Product Browsing",
      "description": "As a Guest User, I want to browse products so that I can discover items without an account.",
      "acceptance_criteria": [
        {
          "text": "Given I am an unauthenticated visitor, When I land on the homepage, Then I can view all available products.",
          "criterion_type": "Given-When-Then"
        }
      ],
      "source_fr_id": 1,
      "label": "FR"
    },
    {
      "title": "Express Checkout with Stripe",
      "description": "As a Customer, I want to pay using Stripe so that my transactions are secure and fast.",
      "acceptance_criteria": [
        {
          "text": "The payment gateway must load the Stripe interface correctly.",
          "criterion_type": "plain"
        }
      ],
      "source_fr_id": 3,
      "label": "FR"
    }
  ],
  "requirements": [
    {
      "id": 1,
      "text": "The system shall allow users to browse products without logging in.",
      "actor": "Guest User",
      "goal": "browse products",
      "source_hint": "browse",
      "label": "FR",
      "confidence": 0.98
    },
    {
      "id": 3,
      "text": "The checkout process must support payments via Stripe.",
      "actor": "Customer",
      "goal": "support payments",
      "source_hint": "Stripe",
      "label": "FR",
      "confidence": 0.95
    }
  ],
  "summary": "This document defines requirements for guest access, shopping cart persistence, and Stripe payment integration.",
  "quality_report": {
    "overall_score": 0.94,
    "traceability_coverage": 1.0,
    "groundedness_score": 0.95,
    "story_completeness": 0.9,
    "acceptance_criteria_quality": 0.95,
    "duplicate_risk": 0.0,
    "requirement_count": 2,
    "story_count": 2,
    "high_severity_issue_count": 0
  },
  "error_message": null,
  "processing_time_ms": 1450
}
```

---

## 🏗️ State Schema Details

For developers extending the pipeline, the following Pydantic models are used:
- **`FunctionalRequirement`**: Basic extracted unit.
- **`ClassifiedRequirement`**: Requirement + `label` (FR/NFR/BR) + `confidence`.
- **`UserStory`**: The final product unit with **Acceptance Criteria**.
