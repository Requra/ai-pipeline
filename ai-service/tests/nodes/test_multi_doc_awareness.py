import pytest
from app.nodes.parse_to_chunks import parse_to_chunks_node
from app.nodes.format import format_node
from app.schemas.items import SourceChunk, ExtractedRequirement, EvidenceSpan, ClassifiedRequirement
from app.schemas.pipeline_state import PipelineState


@pytest.mark.asyncio
async def test_parse_to_chunks_multi_doc():
    state = {
        "job_id": "test-job-multi",
        "file_type": "text",
        "source_documents": [
            {
                "document_id": "doc-a",
                "filename": "requirements_a.pdf",
                "file_type": "pdf",
                "text": "Page 1 of Doc A\fPage 2 of Doc A"
            },
            {
                "document_id": "doc-b",
                "filename": "spec_b.txt",
                "file_type": "text",
                "text": "Slide 1 of Doc B"
            }
        ]
    }

    res = await parse_to_chunks_node(state)
    assert res["status"] == "chunks_parsed"
    chunks = res["chunks"]

    # We expect 2 chunks for Doc A (separated by form-feed \f) and 1 chunk for Doc B
    assert len(chunks) == 3

    # Doc A chunk 1
    assert chunks[0].document_id == "doc-a"
    assert chunks[0].page_number == 1
    assert chunks[0].chunk_id == "chk_test-job-multi_doc-a_p1_c0"

    # Doc A chunk 2
    assert chunks[1].document_id == "doc-a"
    assert chunks[1].page_number == 2
    assert chunks[1].chunk_id == "chk_test-job-multi_doc-a_p2_c1"

    # Doc B chunk 1
    assert chunks[2].document_id == "doc-b"
    assert chunks[2].page_number is None
    assert chunks[2].chunk_id == "chk_test-job-multi_doc-b_pNone_c0"


@pytest.mark.asyncio
async def test_format_node_multi_doc():
    state = {
        "job_id": "test-format-multi",
        "file_type": "text",
        "source_documents": [
            {
                "document_id": "doc-a",
                "filename": "requirements_a.pdf",
                "file_type": "pdf",
                "mime_type": "application/pdf"
            },
            {
                "document_id": "doc-b",
                "filename": "spec_b.txt",
                "file_type": "text",
                "mime_type": "text/plain"
            }
        ],
        "extracted_requirements": [
            ExtractedRequirement(
                id=1,
                text="The system must authenticate users.",
                actor="System",
                goal="authenticate users",
                confidence=0.95,
                evidence=[
                    EvidenceSpan(
                        chunk_id="chk_test-format-multi_doc-a_p1_c0",
                        quote="authenticate users",
                        page_number=1,
                        document_id="doc-a"
                    ),
                    # A legacy fallback evidence without document_id
                    EvidenceSpan(
                        chunk_id="chk_legacy_chunk",
                        quote="legacy user story requirement",
                        page_number=3,
                        document_id=None
                    )
                ]
            )
        ],
        "classified_requirements": [],
        "user_stories": [],
        "quality_issues": [],
        "warnings": [],
        "is_useful": True,
        "status": "success",
        "started_at": 1000.0,
        "processing_time_ms": 100
    }

    # Coerce requirements list
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=1,
            text="The system must authenticate users.",
            actor="System",
            goal="authenticate users",
            confidence=0.95,
            evidence=state["extracted_requirements"][0].evidence,
            labels=["FR"]
        )
    ]

    res = await format_node(state)
    jr = res["job_result"]

    # Verify multiple source documents are mapped in response
    assert len(jr.source_documents) == 2
    assert jr.source_documents[0].source_id == "doc-a"
    assert jr.source_documents[0].file_name == "requirements_a.pdf"
    assert jr.source_documents[1].source_id == "doc-b"
    assert jr.source_documents[1].file_name == "spec_b.txt"

    # Verify requirement source references mapping
    assert len(jr.requirements) == 1
    req = jr.requirements[0]
    assert len(req.source_refs) == 2

    # First ref: resolved from doc-a
    assert req.source_refs[0].source_id == "doc-a"
    assert req.source_refs[0].document_name == "requirements_a.pdf"
    assert req.source_refs[0].page == 1

    # Second ref: legacy fallback
    assert req.source_refs[1].source_id == "SRC-001"
    assert req.source_refs[1].document_name == "unknown"  # falls back to default filename
    assert req.source_refs[1].page == 3
