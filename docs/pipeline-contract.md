# 📜 AI Pipeline Data Contract

This document defines the input and output structure for the AI Requirement Extraction Pipeline. The contract ensures compatibility between the FastAPI interface and the underlying LangGraph execution.

## 📥 Input Contract

The pipeline accepts a `PipelineState` initialization object.

| Field | Type | Description |
| :--- | :--- | :--- |
| `job_id` | `string` | Unique identifier for the processing request. |
| `raw_text` | `string` | The text to be processed (extracted or transcribed). |
| `file_type` | `string` | `pdf`, `docx`, or `audio`. Determines the initial routing. |

### Example Input
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
