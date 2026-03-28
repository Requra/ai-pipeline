# AI Pipeline Contract

The `JobResult` output structure is consistent with the internal `PipelineState` dictionary upon completion. 

## Structure

```json
{
  "file_type": "string",
  "metadata": "object",
  "raw_transcript": "string",
  "extracted_items": [
    {
      "id": "string",
      "content": "string",
      "confidence": "number",
      "source_context": "string"
    }
  ],
  "classifications": ["string"],
  "generated_content": "string",
  "summary": "string",
  "status": "string",
  "error_log": ["string"]
}
```
