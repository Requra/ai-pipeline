import pytest
from app.nodes.detect_file_type import detect_file_type_node
from app.nodes.parse_to_chunks import parse_to_chunks_node
from app.schemas.items import DocumentSource, SourceChunk

@pytest.mark.asyncio
async def test_detect_file_type_pdf():
    state = {
        "raw_bytes": b"%PDF-1.4\nTest PDF content",
        "metadata": {"filename": "test.pdf"}
    }
    result = await detect_file_type_node(state)
    assert result["file_type"] == "pdf"
    assert isinstance(result["source_metadata"], DocumentSource)
    assert result["source_metadata"].mime_type == "application/pdf"
    assert result["status"] == "type_detected"

@pytest.mark.asyncio
async def test_detect_file_type_docx():
    state = {
        "raw_bytes": b"PK\x03\x04\x14\x00\x06\x00", # Minimal zip signature
        "metadata": {"filename": "test.docx"}
    }
    result = await detect_file_type_node(state)
    assert result["file_type"] == "docx"
    assert result["source_metadata"].mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

@pytest.mark.asyncio
async def test_detect_file_type_audio():
    state = {
        "raw_bytes": b"ID3\x03\x00\x00\x00\x00\x00\x00", # Minimal MP3 signature
        "metadata": {"filename": "test.mp3"}
    }
    result = await detect_file_type_node(state)
    assert result["file_type"] == "audio"

@pytest.mark.asyncio
async def test_detect_file_type_text():
    state = {
        "raw_bytes": b"This is a plain text file.",
        "metadata": {"filename": "test.txt"}
    }
    result = await detect_file_type_node(state)
    assert result["file_type"] == "text"
    assert result["source_metadata"].mime_type == "text/plain"

@pytest.mark.asyncio
async def test_detect_file_type_too_large():
    state = {
        "raw_bytes": b"P" * (21 * 1024 * 1024), # 21MB document
        "metadata": {"filename": "large.txt"}
    }
    result = await detect_file_type_node(state)
    assert result["status"] == "rejected"
    assert "too large" in result["error"]

@pytest.mark.asyncio
async def test_parse_to_chunks_pdf():
    state = {
        "job_id": "test_job",
        "file_type": "pdf",
        "raw_text": "Page 1 content\fPage 2 content\fPage 3 content"
    }
    result = await parse_to_chunks_node(state)
    assert result["status"] == "chunks_parsed"
    chunks = result["chunks"]
    assert len(chunks) == 3
    assert chunks[0].page_number == 1
    assert chunks[1].page_number == 2
    assert chunks[2].page_number == 3
    assert "Page 1 content" in chunks[0].text
    assert chunks[0].chunk_id == "chk_test_job_0"

@pytest.mark.asyncio
async def test_parse_to_chunks_text_sliding_window():
    # Long text to trigger sliding window
    long_text = "Word " * 1000 # 5000 chars > 3000 chunk size
    state = {
        "job_id": "test_job",
        "file_type": "text",
        "raw_text": long_text
    }
    result = await parse_to_chunks_node(state)
    assert result["status"] == "chunks_parsed"
    chunks = result["chunks"]
    assert len(chunks) > 1
    # Check overlap
    assert chunks[0].end_char > chunks[1].start_char
    assert (chunks[0].end_char - chunks[1].start_char) == 500 # Default overlap

@pytest.mark.asyncio
async def test_parse_to_chunks_empty():
    state = {
        "file_type": "text",
        "raw_text": ""
    }
    result = await parse_to_chunks_node(state)
    assert result["status"] == "error"
    assert "No text provided" in result["error"]
