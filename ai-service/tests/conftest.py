import pytest
import os
from dotenv import load_dotenv

# Load environment variables for real API calls
load_dotenv()

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
    return {
        "job_id": "test-job-123",
        "error_log": [],
        "status": "started",
        "raw_text": None,
        "functional_requirements": [],
        "classified_requirements": [],
        "user_stories": [],
        "summary": None,
        "is_useful": True,
        "relevance_score": 1.0
    }
