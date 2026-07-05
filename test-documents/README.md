# Pipeline Test Documents

Consolidated input fixtures for manual, API, and end-to-end pipeline testing.
The original files remain under `ai-service/tests`; these copies provide one
stable folder for external test runs.

## Contents

### `text/`

- `sample_srs_crm.txt` — large CRM specification covering functional,
  non-functional, business-rule, scope, risk, and meeting-note extraction.
- `simple_project_brief.txt` — normal relevant project brief.
- `meeting_transcript.txt` — transcript-style requirements.
- `duplicate_requirements.txt` — requirement deduplication.
- `nfr_br_requirements.txt` — NFR and business-rule classification.
- `irrelevant_text.txt` — expected relevance rejection.

### `pdf/`

- `sample.pdf` — basic PDF ingestion.
- `ingest-node-sample.pdf` — alternate PDF ingestion fixture.

### `docx/`

- `sample.docx` — basic DOCX ingestion.
- `ingest-node-sample.docx` — alternate DOCX ingestion fixture.

### `audio/`

- `English.mp3` — English transcription.
- `Arabic.mp3` — Arabic transcription.
- `Kickoff-meeting-Mixed.mp3` — mixed-language meeting transcription.

## Run examples

From the repository root:

```powershell
curl.exe -X POST http://localhost:8000/process `
  -F "file=@test-documents/text/sample_srs_crm.txt;type=text/plain"

curl.exe -X POST http://localhost:8000/process `
  -F "file=@test-documents/pdf/sample.pdf;type=application/pdf"

curl.exe -X POST http://localhost:8000/process `
  -F "file=@test-documents/docx/sample.docx;type=application/vnd.openxmlformats-officedocument.wordprocessingml.document"

curl.exe -X POST http://localhost:8000/process `
  -F "file=@test-documents/audio/English.mp3;type=audio/mpeg"
```

Poll the returned job:

```powershell
curl.exe http://localhost:8000/status/<job_id>
```

