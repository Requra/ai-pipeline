# API Response Contract v1

This document defines the frozen **V1 Public Contract** for the Requra AI Pipeline service response (`PipelineResponseV1` / `JobResult`).

The backend, frontend, and mobile clients should consume only the fields documented below. Internal LangGraph states can evolve freely without breaking these public structures.

## Stability Rules
- Existing fields will not be removed or renamed.
- Field types will remain stable.
- Extra fields may be added in a backward-compatible manner.
- Deprecated fields should not be used in new integrations.

---

## Response Status Enum
The `status` field represents the overall status of the pipeline run and can be one of:
- `completed`: The main expected outputs (requirements and user stories) were generated successfully.
- `partial`: Useful output exists, but one or more nodes failed, skipped, or produced incomplete output (e.g. warnings exist).
- `failed`: The run failed entirely; no useful output could be generated.
- `rejected`: The input was determined to be irrelevant or not useful for software requirements extraction.

*Note: The status `"error"` is no longer used in the public contract.*

---

## Errors and Warnings

### Structured Error Schema (`error`)
When the pipeline experiences a recoverable or non-recoverable error, the `error` object is populated. Otherwise, it is `null`.

```json
{
  "node_name": "extract",
  "code": "EXTRACT_LLM_FAILURE",
  "message": "Extraction failed because the LLM provider was not configured.",
  "recoverable": true
}
```

### Warning Schema (`warnings`)
An array of warnings that occurred during pipeline execution. Always exists as an array (never `null`).

```json
{
  "node_name": "generate",
  "code": "GENERATE_LLM_FAILURE_FALLBACK",
  "message": "Generation LLM failed or output could not be parsed; fallback stories generated."
}
```

---

## Schema Reference

### Source Documents (`source_documents`)
Details of the document(s) uploaded and parsed.
- `source_id`: Stable unique identifier (e.g. `"SRC-001"`).
- `source_type`: One of `"text"`, `"pdf"`, `"docx"`, `"audio"`, `"transcript"`, `"unknown"`.
- `file_name`: String filename.
- `mime_type`: Standard MIME type string.
- `language`: Two-letter language code (default `"en"`).

### Requirements (`requirements`)
- `id`: String identifier formatted as `"REQ-XXX"` (e.g., `"REQ-001"`).
- `title`: Short descriptive title.
- `description`: The full text of the requirement.
- `type`: One of `"Functional"`, `"Non-Functional"`, `"Business"`, `"Unknown"`.
- `category`: String category.
- `priority`: One of `"Low"`, `"Medium"`, `"High"`, `"Critical"`, `"Unknown"`.
- `actor`: The system/user role performing the action.
- `confidence_score`: Float value between `0.0` and `1.0`.
- `deduplication_key`: URL-safe slug for duplicate checking.
- `source_refs`: Traceability links to source chunks.
- `quality`: Contains the quality `score` (0.0 to 1.0) and arrays of quality `issues` and `warnings`.

### User Stories (`user_stories`)
- `id`: String identifier formatted as `"US-XXX"`.
- `requirement_id`: Link to the parent requirement `"REQ-XXX"`.
- `title`: Short title.
- `user_story`: Standard Agile format description (`As a..., I want..., so that...`).
- `acceptance_criteria`: Array of acceptance criterion objects containing `id`, `text`, and `criterion_type`.
- `priority`: Story priority.
- `type`: Story type.
- `deduplication_key`: Deduplication slug.
- `source_refs`: Traceability links to source chunks.
- `quality`: Quality score and issues.
- `jira_fields`: Frozen export representation for Jira.

### Summary (`summary`)
Contains high-level analysis summaries:
- `executive_summary`: Plain text summary.
- `key_decisions`, `open_questions`, `risks`, `assumptions`, `action_items`, `stakeholders`, `scope`, `out_of_scope`: Arrays of strings.

### Exports (`exports`)
Ready-to-use row mappings for tabular exports:
- `excel`: Excel row maps containing standard columns (`id`, `title`, `user_story`, `acceptance_criteria`, `type`, `priority`, `actor`, `source_requirement_id`, `source_refs`).
- `jira`: Jira rows containing fields map.

### Artifacts (`artifacts`)
- `excel_file`: Object containing file download parameters (`available`, `file_url`, `file_name`, `mime_type`).

---

## Examples

### 1. Golden Success Response Example
```json
{
  "contract_version": "1.0",
  "job_id": "postman-test-inventory-001",
  "status": "completed",
  "is_useful": true,
  "relevance_score": 0.95,
  "source_documents": [
    {
      "source_id": "SRC-001",
      "source_type": "text",
      "file_name": "requirements.txt",
      "mime_type": "text/plain",
      "language": "en"
    }
  ],
  "requirements": [
    {
      "id": "REQ-001",
      "title": "Request inventory item",
      "description": "Employees must be able to request inventory items.",
      "type": "Functional",
      "category": "Inventory",
      "priority": "Medium",
      "actor": "Employee",
      "confidence_score": 0.95,
      "deduplication_key": "employee-request-inventory-item",
      "source_refs": [],
      "quality": {
        "score": 1.0,
        "issues": [],
        "warnings": []
      }
    }
  ],
  "user_stories": [
    {
      "id": "US-001",
      "requirement_id": "REQ-001",
      "title": "Request inventory item",
      "user_story": "As an employee, I want to request inventory items, so that I can receive the resources I need.",
      "acceptance_criteria": [
        {
          "id": "US-001_ac_1",
          "text": "Requirement is implemented as specified",
          "criterion_type": "plain"
        }
      ],
      "priority": "Medium",
      "type": "Functional",
      "deduplication_key": "employee-request-inventory-item",
      "source_refs": [],
      "quality": {
        "score": 1.0,
        "issues": [],
        "warnings": []
      },
      "jira_fields": {
        "issue_type": "Story",
        "summary": "Request inventory item",
        "description": "As an employee, I want to request inventory items, so that I can receive the resources I need.",
        "acceptance_criteria": ["Requirement is implemented as specified"],
        "priority": "Medium",
        "labels": ["FR"],
        "components": [],
        "epic_name": "",
        "story_points": 0
      }
    }
  ],
  "requirement_coverages": [
    {
      "requirement_id": "REQ-001",
      "coverage_type": "covered_by_story",
      "story_ids": ["US-001"],
      "acceptance_criteria_ids": ["US-001_ac_1"],
      "reason": null
    }
  ],
  "summary": {
    "executive_summary": "System supports standard request processes.",
    "key_decisions": [],
    "open_questions": [],
    "risks": [],
    "assumptions": [],
    "action_items": [],
    "stakeholders": [],
    "scope": [],
    "out_of_scope": []
  },
  "exports": {
    "excel": {
      "available": true,
      "columns": ["id", "title", "user_story", "acceptance_criteria", "type", "priority", "actor", "source_requirement_id", "source_refs"],
      "rows": [
        {
          "id": "US-001",
          "title": "Request inventory item",
          "user_story": "As an employee, I want to request inventory items, so that I can receive the resources I need.",
          "acceptance_criteria": "Requirement is implemented as specified",
          "type": "Functional",
          "priority": "Medium",
          "actor": "Employee",
          "source_requirement_id": "REQ-001",
          "source_refs": "[]"
        }
      ]
    },
    "jira": {
      "available": true,
      "issue_type": "Story",
      "rows": [
        {
          "issue_type": "Story",
          "summary": "Request inventory item",
          "description": "As an employee, I want to request inventory items, so that I can receive the resources I need.",
          "acceptance_criteria": ["Requirement is implemented as specified"],
          "priority": "Medium",
          "labels": ["FR"],
          "components": [],
          "epic_name": "",
          "story_points": 0,
          "source_requirement_id": "REQ-001"
        }
      ]
    }
  },
  "artifacts": {
    "excel_file": {
      "available": false,
      "file_url": "",
      "file_name": "",
      "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    }
  },
  "quality_issues": [],
  "warnings": [],
  "error": null,
  "processing_time_ms": 125
}
```

### 2. Partial Failure Response Example
```json
{
  "contract_version": "1.0",
  "job_id": "postman-test-inventory-002",
  "status": "partial",
  "is_useful": true,
  "relevance_score": 0.88,
  "source_documents": [],
  "requirements": [],
  "user_stories": [],
  "requirement_coverages": [],
  "summary": null,
  "exports": {
    "excel": { "available": false, "columns": [], "rows": [] },
    "jira": { "available": false, "issue_type": "Story", "rows": [] }
  },
  "artifacts": {
    "excel_file": {
      "available": false,
      "file_url": "",
      "file_name": "",
      "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    }
  },
  "quality_issues": [],
  "warnings": [
    {
      "node_name": "extract",
      "code": "EXTRACT_EMPTY",
      "message": "No requirements found in the provided content."
    }
  ],
  "error": {
    "node_name": "generate",
    "code": "GENERATE_FAILURE",
    "message": "Internal processing timeout on LLM query.",
    "recoverable": true
  },
  "processing_time_ms": 4200
}
```

### 3. Failed Response Example
```json
{
  "contract_version": "1.0",
  "job_id": "postman-test-inventory-003",
  "status": "failed",
  "is_useful": true,
  "relevance_score": 0.0,
  "source_documents": [],
  "requirements": [],
  "user_stories": [],
  "requirement_coverages": [],
  "summary": null,
  "exports": {
    "excel": { "available": false, "columns": [], "rows": [] },
    "jira": { "available": false, "issue_type": "Story", "rows": [] }
  },
  "artifacts": {
    "excel_file": {
      "available": false,
      "file_url": "",
      "file_name": "",
      "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    }
  },
  "quality_issues": [],
  "warnings": [],
  "error": {
    "node_name": "extract",
    "code": "EXTRACT_FAILURE",
    "message": "No chunks or raw text provided",
    "recoverable": false
  },
  "processing_time_ms": 5
}
```

---

## Deprecated Fields
The following fields are kept only for backward compatibility with earlier client libraries or test runners. They **MUST NOT** be used for new integrations and are planned to be removed in v2:

- `export_rows`: Kept for backward compatibility with Excel generators expecting a flat format. Superceded by the `exports` object.
- `error_message`: Plain string representation of error message. Superceded by the structured `error` object.
