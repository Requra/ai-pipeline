# Requra AI Pipeline API Contract Guide

This document defines the input contracts and request options for both the local/development endpoints and the internal production API.

For detailed schemas and payload examples of the output formats, refer to the frozen **[API Response Contract v1](contracts/pipeline-response-v1.md)**.

---

## 📥 Input Contracts

### 1. Local/Development Endpoints (`POST /process`, `POST /process-json`)
These endpoints accept the basic `PipelineState` initialization parameters.

| Field | Type | Description |
| :--- | :--- | :--- |
| `job_id` | `string` | Unique identifier for the processing request (optional, auto-generated if omitted). |
| `raw_text` | `string` | The plain text to be processed (required for `/process-json`). |
| `file_type` | `string` | `pdf` \| `docx` \| `audio`. Determines the file parsing routing. |

#### Example Input (Dev/Demo)
```json
{
  "job_id": "req-9921",
  "raw_text": "The system shall allow users to browse products without logging in. Checkout must support Stripe.",
  "file_type": "pdf"
}
```

### 2. Internal Production API (`POST /internal/jobs`)
For durable background execution in production, the internal endpoint accepts a Bearer token (`Authorization: Bearer <AI_INTERNAL_SERVICE_TOKEN>`) and the following payload:

| Field | Type | Description |
| :--- | :--- | :--- |
| `job_id` | `string` | Unique identifier for the job (`^[A-Za-z0-9._-]{1,128}$`). |
| `tenant_id` | `string` | Tenant ID for logical data partitioning. |
| `project_id` | `string` | Project ID for grouping documents and requirements. |
| `requested_by` | `string` | User ID initiating the request. |
| `input_type` | `string` | `text` \| `backend_document` \| `backend_transcript` \| `backend_audio`. |
| `content` | `string` | Inline text content (required for `text` or `backend_transcript`). |
| `source_documents` | `array` | List of reference documents to parse (required for `backend_document` or `backend_audio`). |
| `options` | `object` | Run-time execution options (see below). |

#### Production Run Options (`options`):
* `generate_user_stories` (`boolean`): If `true`, generates Given-When-Then Agile user stories.
* `generate_summary` (`boolean`): If `true`, generates structured executive summaries.
* `enable_embeddings` (`boolean`): If `true`, generates and stores semantic vectors via `pgvector`.
* `enable_hybrid_retrieval` (`boolean`): If `true`, combines BM25 and vector similarity search.
* `callback_url` (`string`): Webhook endpoint to notify the backend upon job completion or failure.

#### Example Input (Production)
```json
{
  "job_id": "be-job-123",
  "tenant_id": "tenant-1",
  "project_id": "project-9",
  "requested_by": "user-42",
  "input_type": "backend_document",
  "source_documents": [
    {
      "document_id": "D-1",
      "file_type": "pdf",
      "mime_type": "application/pdf",
      "storage_key": "s3://requra-docs/brief.pdf",
      "file_url": "https://requra-docs.s3.amazonaws.com/brief.pdf"
    }
  ],
  "options": {
    "generate_user_stories": true,
    "generate_summary": true,
    "enable_embeddings": true,
    "enable_hybrid_retrieval": true,
    "callback_url": "https://backend.requra.ai/callbacks/jobs/be-job-123"
  }
}
```

---

## 📤 Output Contracts & Responses

All endpoints eventually return or callback with a `JobResult` structured model.
* **Success Output**: Includes extracted requirements, user stories, coverage maps, executive summary, and optional `quality_report` metrics.
* **Failure Output**: Populated with a structured `error` object and an array of `warnings`.

Please consult the **[API Response Contract v1](contracts/pipeline-response-v1.md)** for a complete schema field reference and response examples (Golden Success, Partial Failure, and System Failure).
