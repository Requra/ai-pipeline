import hashlib
from app.prompts.registry import PromptId, PROMPT_MAP, get_template_path

# Expected SHA-256 hashes for each prompt version, computed over the template
# content with line endings normalized to LF.
#
# Line-ending normalization is deliberate: on Windows checkouts `core.autocrlf`
# rewrites these files to CRLF in the working tree while git stores them as LF,
# so hashing raw bytes would be non-deterministic across platforms. Normalizing
# to LF makes the snapshot stable everywhere.
#
# If you intentionally change a prompt, update its hash here.
EXPECTED_HASHES = {
    PromptId.CLASSIFY_REQUIREMENTS_V1: "35A6B9A36C83616BBAD4540EDBDBE76A4ACEE00407F72896BA54D7B8F2C6B2DF",
    PromptId.EXTRACT_REQUIREMENTS_V1: "883672CAD2CA84F1B774BA40AA7FEAA3F2AEC7264AD7AE1E9B227A3C090F9635",
    PromptId.EXTRACT_REQUIREMENTS_V2: "78F197E868F148FEA3ACA9301CEFA3876F9C7C5FEFAB6F2EF1D2205F413E4239",
    PromptId.GENERATE_USER_STORIES_V1: "32A55420033FDD8D8636B0D780332D0858B8B12472C17244771000E51E650B5E",
    PromptId.GENERATE_USER_STORIES_V2: "7BADB9E4F5F35F1F9E2CA7F52419C8B74502258AA732FD4C1007B4F21EA13B0F",
    PromptId.INGEST_RELEVANCE_V1: "1990EA6DF9D3BC1576B32952B9C70A39579645A11DEFB8DE379D0D7384FD46E5",
    PromptId.SUMMARIZE_STRUCTURED_V1: "13F0D3217EEA6D278DCFD66F3C11401D0A10FA88AC746ACB76A4E4CFC3CF51C8",
    PromptId.DETECT_CONFLICTS_V1: "F35CA4E84F46BBE5CEF91992149D4D87D628A1007E683D57036AA04AA12AF0C4",
    PromptId.REGENERATE_STORY_V1: "F8C9496AABF0610B8813AC1F66AE413B832068EBF50FBEBD9E73A7B9D4A61EA5",
    PromptId.REPAIR_STORIES_V1: "479D8C3B83E26BC68AE5EC1261F9A033523595C14B3AD6F1ED725352D3EA4372",
}


def _normalized_hash(path) -> str:
    raw = path.read_bytes()
    # Normalize CRLF/CR -> LF so the hash is independent of git autocrlf.
    normalized = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(normalized).hexdigest().upper()


def test_prompt_snapshots():
    """Verify prompt templates have not changed (line-ending independent)."""
    for prompt_id, expected_hash in EXPECTED_HASHES.items():
        path = get_template_path(prompt_id)
        assert path.exists(), f"Prompt template missing: {path}"
        actual_hash = _normalized_hash(path)
        assert actual_hash == expected_hash, (
            f"Prompt '{prompt_id}' has changed!\n"
            f"Expected: {expected_hash}\n"
            f"Actual:   {actual_hash}\n"
            "If this change was intentional, update the hash in "
            "test_prompt_snapshots.py after reviewing the new prompt."
        )


def test_every_prompt_has_a_snapshot():
    """Every registered prompt must be snapshot-protected."""
    for prompt_id in PROMPT_MAP:
        assert prompt_id in EXPECTED_HASHES, (
            f"Prompt '{prompt_id}' is registered but has no snapshot hash."
        )
