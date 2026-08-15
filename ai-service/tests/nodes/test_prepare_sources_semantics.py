import pytest
from app.nodes.prepare_sources import prepare_sources_node
from app.schemas.items import SourceChunk, DocumentSource
from app.services.source_processing.models import ProcessedSource


@pytest.mark.asyncio
async def test_prepare_sources_all_irrelevant(monkeypatch):
    """When all sources are software-irrelevant, outcome must be REJECTED."""
    async def _mock_process_doc(src, job_id, **kwargs):
        return ProcessedSource(
            document_id=src.document_id,
            filename=src.filename,
            source_type="document",
            status="rejected",
            raw_text="Irrelevant grocery recipe",
            is_useful=False,
            error_message="Not software related",
        )

    import app.nodes.prepare_sources as ps
    monkeypatch.setattr(ps, "process_single_source", _mock_process_doc)

    state = {
        "job_id": "all-irrelevant-1",
        "raw_inputs": [
            {"document_id": "d1", "filename": "doc1.txt", "raw_bytes": b"recipe", "file_type": "text"},
            {"document_id": "d2", "filename": "doc2.txt", "raw_bytes": b"shopping list", "file_type": "text"},
        ],
    }

    res = await prepare_sources_node(state)
    assert res["status"] == "rejected"
    assert res["is_useful"] is False
    assert "DOCUMENT_REJECTED" in res["error"]


@pytest.mark.asyncio
async def test_prepare_sources_all_technical_failures(monkeypatch):
    """When all sources suffer technical failures, outcome must be FAILED."""
    async def _mock_process_doc(src, job_id, **kwargs):
        return ProcessedSource(
            document_id=src.document_id,
            filename=src.filename,
            source_type="document",
            status="failed",
            error_code="PARSE_FAILED",
            error_message="Corrupted document",
        )

    import app.nodes.prepare_sources as ps
    monkeypatch.setattr(ps, "process_single_source", _mock_process_doc)

    state = {
        "job_id": "all-failed-1",
        "raw_inputs": [
            {"document_id": "d1", "filename": "bad1.pdf", "raw_bytes": b"corrupt1", "file_type": "pdf"},
            {"document_id": "d2", "filename": "bad2.docx", "raw_bytes": b"corrupt2", "file_type": "docx"},
        ],
    }

    res = await prepare_sources_node(state)
    assert res["status"] == "failed"
    assert res["is_useful"] is False
    assert "ALL_SOURCES_FAILED" in res["error"]


@pytest.mark.asyncio
async def test_prepare_sources_rejected_plus_technical_failure(monkeypatch):
    """When one source is irrelevant and another suffers technical failure, must be FAILED, NOT REJECTED."""
    async def _mock_process_doc(src, job_id, **kwargs):
        if src.document_id == "d1":
            return ProcessedSource(
                document_id="d1",
                filename="recipe.txt",
                source_type="document",
                status="rejected",
                raw_text="Baking bread",
                is_useful=False,
                error_message="Not software related",
            )
        else:
            return ProcessedSource(
                document_id="d2",
                filename="meeting.mp3",
                source_type="audio",
                status="failed",
                error_code="STT_ALL_PROVIDERS_FAILED",
                error_message="STT provider unreachable",
            )

    import app.nodes.prepare_sources as ps
    monkeypatch.setattr(ps, "process_single_source", _mock_process_doc)

    state = {
        "job_id": "rej-fail-1",
        "raw_inputs": [
            {"document_id": "d1", "filename": "recipe.txt", "raw_bytes": b"recipe", "file_type": "text"},
            {"document_id": "d2", "filename": "meeting.mp3", "raw_bytes": b"audio", "file_type": "audio"},
        ],
    }

    res = await prepare_sources_node(state)
    assert res["status"] == "failed"
    assert res["is_useful"] is False
    assert "ALL_SOURCES_FAILED" in res["error"]


@pytest.mark.asyncio
async def test_prepare_sources_ready_plus_technical_failure(monkeypatch):
    """When one source is ready and one fails, must continue with partial failure."""
    async def _mock_process_doc(src, job_id, **kwargs):
        if src.document_id == "d1":
            chunk = SourceChunk(chunk_id="c1", document_id="d1", text="The user must login.", start_char=0, end_char=20)
            return ProcessedSource(
                document_id="d1",
                filename="spec.pdf",
                source_type="pdf",
                status="ready",
                raw_text="The user must login.",
                is_useful=True,
                chunks=[chunk],
            )
        else:
            return ProcessedSource(
                document_id="d2",
                filename="corrupt.docx",
                source_type="docx",
                status="failed",
                error_code="PARSE_FAILED",
                error_message="Corrupted file",
            )

    import app.nodes.prepare_sources as ps
    monkeypatch.setattr(ps, "process_single_source", _mock_process_doc)

    state = {
        "job_id": "ready-fail-1",
        "raw_inputs": [
            {"document_id": "d1", "filename": "spec.pdf", "raw_bytes": b"pdf", "file_type": "pdf"},
            {"document_id": "d2", "filename": "corrupt.docx", "raw_bytes": b"docx", "file_type": "docx"},
        ],
    }

    res = await prepare_sources_node(state)
    assert res["status"] == "sources_prepared"
    assert res["is_useful"] is True
    assert res["partial_source_failure"] is True
    assert len(res["chunks"]) == 1
    assert any(w["code"] == "PARTIAL_SOURCE_FAILURE" for w in res["warnings"])
