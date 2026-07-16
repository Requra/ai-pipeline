# Multipart binary upload fixtures

`customer_workspace_requirements.docx` and `operations_case_management.pdf` are
real, multi-page binary fixtures for `POST /internal/process` tests. Both contain
software requirements before and after a deliberately unrelated middle section.
The test asserts that each source is independently extracted, chunked, persisted,
and returned with its own source identity; it does not imply that unrelated prose
should be turned into a requirement by an LLM.

Regenerate the fixtures with the bundled workspace Python runtime:

```powershell
C:\Users\shawk\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe scripts\create_multipart_upload_fixtures.py
```
