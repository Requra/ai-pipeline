import io
import zipfile
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.config import settings
from app.store.factory import get_stores
from app.services.file_inspection import detect_mime_and_type, is_valid_pdf, is_valid_docx, is_valid_mp3

AUTH_HEADERS = {"Authorization": f"Bearer {settings.AI_INTERNAL_SERVICE_TOKEN}"}


@pytest.fixture(autouse=True)
def _mock_background_dispatch(monkeypatch):
    from app.worker import dispatch
    async def fake_dispatch(*args, **kwargs):
        return True

    monkeypatch.setattr(dispatch, "dispatch_job", fake_dispatch)
    from app.api import service
    monkeypatch.setattr(service, "dispatch_job", fake_dispatch)


def create_arbitrary_zip() -> bytes:
    """Create a valid ZIP archive that does NOT contain DOCX OOXML structure."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("test.txt", "This is just a regular text file inside a zip")
        z.writestr("data.bin", b"\x00\x01\x02\x03")
    return buf.getvalue()


def create_minimal_valid_docx() -> bytes:
    """Create a minimal valid DOCX structure with [Content_Types].xml and word/document.xml."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("word/document.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Requirements content</w:t></w:r></w:p></w:body></w:document>')
    return buf.getvalue()


def create_minimal_valid_pdf() -> bytes:
    """Create a minimal valid PDF byte sequence."""
    return (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >> endobj\n"
        b"4 0 obj << /Length 44 >> stream\n"
        b"BT /F1 12 Tf 100 700 Td (Requirements) Tj ET\n"
        b"endstream endobj\n"
        b"xref\n0 5\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n0000000214 00000 n \n"
        b"trailer << /Size 5 /Root 1 0 R >>\nstartxref\n308\n%%EOF\n"
    )


def create_minimal_valid_mp3() -> bytes:
    """Create a minimal valid MP3 byte sequence with ID3 header."""
    return b"ID3\x03\x00\x00\x00\x00\x00\x00\xff\xfb\x90d\x00\x00\x00\x00"


@pytest.mark.asyncio
async def test_file_inspection_unit_matrix():
    # 1. PDF Spoofing Checks
    assert not is_valid_pdf(b"Just plain text disguised as a PDF")
    assert not is_valid_pdf(b"MZ\x90\x00\x03\x00\x00\x00PE binary payload")
    assert not is_valid_pdf(create_arbitrary_zip())
    assert is_valid_pdf(create_minimal_valid_pdf())

    # 2. DOCX Spoofing Checks
    assert not is_valid_docx(b"Random binary bytes")
    assert not is_valid_docx(create_arbitrary_zip())  # Arbitrary ZIP is NOT a DOCX!
    assert is_valid_docx(create_minimal_valid_docx())

    # 3. MP3 Spoofing Checks
    assert not is_valid_mp3(b"Random text disguised as mp3")
    assert not is_valid_mp3(create_minimal_valid_pdf())
    assert is_valid_mp3(create_minimal_valid_mp3())

    # 4. detect_mime_and_type Mismatch detection
    # Random text named .pdf -> unknown
    ft, _, _ = detect_mime_and_type(b"Plain text", filename="fake_requirements.pdf")
    assert ft == "unknown"

    # Arbitrary zip named .docx -> unknown
    ft, _, _ = detect_mime_and_type(create_arbitrary_zip(), filename="fake.docx")
    assert ft == "unknown"

    # PDF bytes named .docx -> unknown
    ft, _, _ = detect_mime_and_type(create_minimal_valid_pdf(), filename="spoofed.docx")
    assert ft == "unknown"

    # PDF bytes named .mp3 -> unknown
    ft, _, _ = detect_mime_and_type(create_minimal_valid_pdf(), filename="spoofed.mp3")
    assert ft == "unknown"

    # EXE extension -> unknown
    ft, _, _ = detect_mime_and_type(b"MZ\x90\x00payload", filename="malware.exe")
    assert ft == "unknown"


@pytest.mark.parametrize(
    "filename, content, content_type",
    [
        ("fake_requirements.pdf", b"This is fake text disguised as a PDF requirements doc", "application/pdf"),
        ("malicious.pdf", b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00PE executable binary", "application/pdf"),
        ("arbitrary_zip.pdf", create_arbitrary_zip(), "application/pdf"),
        ("audio_as.pdf", create_minimal_valid_mp3(), "application/pdf"),
        ("fake_notes.docx", b"Plain text disguised as a docx file", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("arbitrary_zip.docx", create_arbitrary_zip(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("pdf_as.docx", create_minimal_valid_pdf(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("pdf_as.mp3", create_minimal_valid_pdf(), "audio/mpeg"),
        ("docx_as.mp3", create_minimal_valid_docx(), "audio/mpeg"),
        ("fake_audio.mp3", b"Not an audio file, just random binary bytes \x01\x02\x03\x04", "audio/mpeg"),
        ("executable.exe", b"MZ\x90\x00\x03\x00\x00\x00PE executable binary", "application/octet-stream"),
    ],
)
@pytest.mark.asyncio
async def test_api_synchronous_415_on_spoofed_or_invalid_media(filename, content, content_type):
    """Ensure POST /internal/process synchronously returns 415 Unsupported Media Type for spoofed files."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        job_id = f"sec-test-spoof-{abs(hash(filename))}"
        files = [
            ("files", (filename, content, content_type)),
        ]
        data = {
            "job_id": job_id,
            "tenant_id": "test-sec-tenant",
            "project_id": "test-sec-project",
        }

        resp = await client.post("/internal/process", headers=AUTH_HEADERS, data=data, files=files)
        assert resp.status_code == 415, f"Expected 415 for {filename}, got {resp.status_code}: {resp.text}"

        # Verify 0 jobs were persisted in the database
        stores = get_stores()
        persisted_job = await stores.jobs.get_job(job_id)
        assert persisted_job is None, f"Job {job_id} must NOT be persisted for 415 rejections!"


@pytest.mark.asyncio
async def test_api_accepts_valid_control_files():
    """Ensure POST /internal/process accepts genuine valid PDF, DOCX, TXT, and MP3 files."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Valid PDF
        resp_pdf = await client.post(
            "/internal/process",
            headers=AUTH_HEADERS,
            data={"job_id": "valid-pdf-job-1", "tenant_id": "t1", "project_id": "p1"},
            files=[("files", ("valid.pdf", create_minimal_valid_pdf(), "application/pdf"))],
        )
        assert resp_pdf.status_code == 202, f"Expected 202 for valid PDF, got {resp_pdf.status_code}: {resp_pdf.text}"

        # Valid DOCX
        resp_docx = await client.post(
            "/internal/process",
            headers=AUTH_HEADERS,
            data={"job_id": "valid-docx-job-1", "tenant_id": "t1", "project_id": "p1"},
            files=[("files", ("valid.docx", create_minimal_valid_docx(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))],
        )
        assert resp_docx.status_code == 202, f"Expected 202 for valid DOCX, got {resp_docx.status_code}: {resp_docx.text}"

        # Valid TXT
        resp_txt = await client.post(
            "/internal/process",
            headers=AUTH_HEADERS,
            data={"job_id": "valid-txt-job-1", "tenant_id": "t1", "project_id": "p1"},
            files=[("files", ("valid.txt", b"REQ-001: The system shall validate user credentials on login.", "text/plain"))],
        )
        assert resp_txt.status_code == 202, f"Expected 202 for valid TXT, got {resp_txt.status_code}: {resp_txt.text}"

        # Valid MP3
        resp_mp3 = await client.post(
            "/internal/process",
            headers=AUTH_HEADERS,
            data={"job_id": "valid-mp3-job-1", "tenant_id": "t1", "project_id": "p1"},
            files=[("files", ("valid.mp3", create_minimal_valid_mp3(), "audio/mpeg"))],
        )
        assert resp_mp3.status_code == 202, f"Expected 202 for valid MP3, got {resp_mp3.status_code}: {resp_mp3.text}"

        # Valid Arabic / Unicode TXT
        arabic_text = "متطلبات النظام: يجب على النظام توفير استعادة كلمة المرور عبر البريد الإلكتروني.".encode("utf-8")
        resp_arabic = await client.post(
            "/internal/process",
            headers=AUTH_HEADERS,
            data={"job_id": "valid-arabic-job-1", "tenant_id": "t1", "project_id": "p1"},
            files=[("files", ("arabic_reqs.txt", arabic_text, "text/plain"))],
        )
        assert resp_arabic.status_code == 202, f"Expected 202 for valid Arabic TXT, got {resp_arabic.status_code}: {resp_arabic.text}"


@pytest.mark.asyncio
async def test_api_rejects_riff_non_wave():
    """Reject RIFF file that is AVI or non-WAVE container when uploaded as .wav."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        fake_avi = b"RIFF\x20\x00\x00\x00AVI LIST\x00"
        resp = await client.post(
            "/internal/process",
            headers=AUTH_HEADERS,
            data={"job_id": "fake-avi-job-1", "tenant_id": "t1", "project_id": "p1"},
            files=[("files", ("fake_audio.wav", fake_avi, "audio/wav"))],
        )
        assert resp.status_code == 415


@pytest.mark.asyncio
async def test_api_rejects_binary_disguised_as_text():
    """Reject binary payload with excessive control characters uploaded as .txt."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        binary_text = b"Some text\x01\x02\x03\x04\x05\x06\x07\x08\x0b\x0c\x0e\x0f" * 10
        resp = await client.post(
            "/internal/process",
            headers=AUTH_HEADERS,
            data={"job_id": "fake-txt-job-1", "tenant_id": "t1", "project_id": "p1"},
            files=[("files", ("binary.txt", binary_text, "text/plain"))],
        )
        assert resp.status_code == 415
