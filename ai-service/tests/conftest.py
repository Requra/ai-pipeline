import pytest
import os
from dotenv import load_dotenv

# Load environment variables for real API calls
load_dotenv()

# Force tests to use the in-memory store and in-process runner.  Keep explicit
# empty values in the environment: app.config also calls load_dotenv() during
# import, and deleting these keys lets that second load silently re-enable the
# developer's real Postgres/Redis services for the unit suite.
os.environ["DATABASE_URL"] = ""
os.environ["REDIS_URL"] = ""

@pytest.fixture
def sample_pdf_bytes():
    # Return binary content for a real PDF test 
    # (In a real scenario, read from fixtures/sample.pdf)
    path = os.path.join(os.path.dirname(__file__), "fixtures", "sample.pdf")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    return b"%PDF-1.4\n1 0 obj\n<< /Title (Test) >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF"

@pytest.fixture
def sample_docx_bytes():
    path = os.path.join(os.path.dirname(__file__), "fixtures", "sample.docx")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    return b"fake-docx-bytes"

@pytest.fixture
def sample_audio_bytes():
    path = os.path.join(os.path.dirname(__file__), "fixtures", "sample.mp3")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    return b"fake-mp3-bytes"

@pytest.fixture
def base_state():
    import time
    return {
        "job_id": "test-job-123",
        "raw_bytes": b"",
        "raw_text": None,
        "file_type": "text",
        "metadata": {},
        "source_metadata": None,
        "chunks": [],
        "extracted_requirements": [],
        "functional_requirements": [],
        "classified_requirements": [],
        "requirement_coverages": [],
        "user_stories": [],
        "quality_issues": [],
        "warnings": [],
        "export_rows": [],
        "summary": None,
        "is_useful": True,
        "relevance_score": 1.0,
        "status": "started",
        "error": None,
        "started_at": time.time(),
        "processing_time_ms": 0
    }
