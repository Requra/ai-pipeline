# Node Guide: Ingest Node
**Status**: `[UNASSIGNED]`  
**Owner Role**: Data Engineer / Backend Developer

## 1. Description & Vision
The **Ingest Node** is the entry point of the entire AI pipeline. Its mission is to take raw, unstructured input (PDFs, DOCX, Text, or Audio markers) and transform them into a clean, standardized string (`raw_text`) that downstream LLM nodes can process. 

**Vision**: A robust gateway that handles any file format, ensures data privacy (PII scrubbing), and provides high-quality text extraction that preserves document structure.

## 2. Current Implementation (`ingest.py`)
- **Logic**: 
    - Detects `file_type`.
    - If `audio`, it passes control to the Transcribe node (returns `raw_text: None`).
    - If `pdf` or `docx`, it uses mock extraction functions.
- **Input**: `state.get("raw_bytes")`, `state.get("file_type")`.
- **Output**: `{"raw_text": str}` or `{"error": str}`.

## 3. Expected Enhancements (TODOs)
- [ ] **Real Extraction**: Replace `extract_pdf` and `extract_docx` mocks with libraries like `PyMuPDF` (fitz) or `python-docx`.
- [ ] **Validation**: Add more rigorous checks for file corruption or empty files.
- [ ] **PII Masking**: Implement a basic regex-based PII scrubber (emails, phone numbers) before passing text to the LLM.
- [ ] **Encoding Handling**: Ensure robust handling of different text encodings (UTF-8, Latin-1) to avoid `UnicodeDecodeError`.

## 4. Operational Guidelines
- **Idempotency**: The node should be able to re-run on the same input without side effects.
- **Error Propagation**: Use the `INGEST_FAILED` prefix in error messages for easy debugging in the final logs.
- **Format Agnostic**: Downstream nodes **must not** know if the content came from a PDF or a Word doc. Only return clean text.

## 5. Verification Checklist
- [ ] Does it handle multi-page PDFs correctly?
- [ ] Does it return an error if the extracted text is too short (< 50 chars)?
- [ ] Is the output `raw_text` trimmed of unnecessary whitespace?
- [ ] Does it fail gracefully when `raw_bytes` is missing?
