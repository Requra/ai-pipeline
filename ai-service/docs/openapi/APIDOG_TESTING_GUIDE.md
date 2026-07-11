# Requra.AI Internal API Integration - APIDOG Testing Guide

This guide describes how to test, verify, and integrate the internal API compatibility endpoints and document recovery tools for the Requra.AI AI pipeline.

---

## Authentication

All endpoints under the `/internal` prefix require bearer token authentication. Set the authorization header as follows:

```http
Authorization: Bearer <AI_INTERNAL_SERVICE_TOKEN>
```

---

## Endpoints

### 1. Compatibility Upload (Multipart/Form-Data)
`POST /internal/process`

Enables compatibility uploads of binary documents (PDF, DOCX) and audio files (MP3, WAV, OGG, M4A, WEBM). Files are parsed, classified, size-validated, and enqueued.

#### Parameters:
* `file` (File, required): The document or audio file binary payload.
* `job_id` (string, required): Stable unique identity for the job.
* `project_id` (string, required): Associated project ID.
* `tenant_id` (string, optional): Associated tenant ID.
* `requested_by` (string, optional): Identity of the user requesting the run.
* `document_id` (string, optional): Stable document identifier (if omitted, a hash-derived stable ID is generated).
* `metadata` (string, optional): JSON-serialized key-value string.
* `callback_url` (string, optional): Backend webhook callback URL.
* `language` (string, optional): Preferred language code (default `en`).
* `reprocess` (boolean, optional): Set to `true` to force reprocess on matching job ID.

#### Curl Example:
```bash
curl -X POST "http://localhost:8000/internal/process" \
  -H "Authorization: Bearer test-internal-token" \
  -F "file=@/path/to/meeting.mp3" \
  -F "job_id=job-audio-101" \
  -F "project_id=project-abc" \
  -F "tenant_id=tenant-xyz" \
  -F "metadata={\"context\":\"QA Test\"}" \
  -F "language=en"
```

---

### 2. Compatibility Text Submission (JSON)
`POST /internal/process-json`

Enables submitting plain text or meeting transcript content directly.

#### Parameters:
* `job_id` (string, required): Stable unique identity for the job.
* `project_id` (string, required): Associated project ID.
* `tenant_id` (string, optional): Associated tenant ID.
* `requested_by` (string, optional): User requesting the run.
* `source_type` (string, optional): One of `"text"`, `"meeting_transcript"`.
* `content` (string, required): The plain text requirements or transcript.
* `metadata` (object, optional): Arbitrary metadata dictionary.
* `options` (object, optional): Job options (e.g. `generate_user_stories`, `language`).
* `reprocess` (boolean, optional): Force reprocessing.

#### Curl Example:
```bash
curl -X POST "http://localhost:8000/internal/process-json" \
  -H "Authorization: Bearer test-internal-token" \
  -H "Content-Type: application/json" \
  -d '{
    "job_id": "job-text-202",
    "project_id": "project-abc",
    "tenant_id": "tenant-xyz",
    "source_type": "text",
    "content": "The system must support two-factor authentication via SMS.",
    "options": {
      "generate_user_stories": true,
      "language": "en"
    }
  }'
```

---

### 3. Document Content Recovery
`GET /internal/documents/{document_id}/content`

Retrieves the raw binary content of a source document from the local mock cache, transient cache, or object-storage origin. Used for troubleshooting, audit logging, and job retries.

#### Curl Example:
```bash
curl -X GET "http://localhost:8000/internal/documents/doc-custom-id/content" \
  -H "Authorization: Bearer test-internal-token" \
  --output downloaded_doc.pdf
```

---

### 4. Production Job Dispatch
`POST /internal/jobs`

Canonical endpoint for enqueuing jobs with reference documents.

#### Curl Example:
```bash
curl -X POST "http://localhost:8000/internal/jobs" \
  -H "Authorization: Bearer test-internal-token" \
  -H "Content-Type: application/json" \
  -d '{
    "job_id": "job-prod-303",
    "project_id": "project-abc",
    "tenant_id": "tenant-xyz",
    "input_type": "backend_document",
    "source_documents": [
      {
        "document_id": "doc-ref-1",
        "file_url": "https://s3.amazonaws.com/requra-bucket/specs.docx",
        "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
      }
    ],
    "options": {
      "generate_user_stories": true
    }
  }'
```

---

### 5. Job Retry
`POST /internal/jobs/{job_id}/retry`

Retries a failed or cancelled job under a new attempt.

#### Curl Example:
```bash
curl -X POST "http://localhost:8000/internal/jobs/job-prod-303/retry" \
  -H "Authorization: Bearer test-internal-token"
```
