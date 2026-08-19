from app.services.file_inspection import detect_mime_and_type, is_valid_webm


EBML_HEADER = b"\x1a\x45\xdf\xa3"


def test_accepts_webm_doctype() -> None:
    raw_bytes = EBML_HEADER + b"\x00" * 12 + b"webm" + b"\x00" * 16

    assert is_valid_webm(raw_bytes)
    assert detect_mime_and_type(raw_bytes, "recording.webm") == (
        "audio",
        "audio/webm",
        "webm",
    )


def test_accepts_cloudinary_matroska_doctype_as_webm_audio() -> None:
    raw_bytes = EBML_HEADER + b"\x00" * 12 + b"matroska" + b"\x00" * 16

    assert is_valid_webm(raw_bytes)
    assert detect_mime_and_type(raw_bytes, "recording-final.webm") == (
        "audio",
        "audio/webm",
        "webm",
    )


def test_rejects_ebml_with_unknown_doctype() -> None:
    raw_bytes = EBML_HEADER + b"\x00" * 12 + b"unsupported" + b"\x00" * 16

    assert not is_valid_webm(raw_bytes)

