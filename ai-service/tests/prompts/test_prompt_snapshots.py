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
    PromptId.EXTRACT_REQUIREMENTS_V1: "C5488794A6A9B8A9EEE881FE6CB0392354B7300E9EFF5B9412491A7C5B5DA92F",
    PromptId.EXTRACT_REQUIREMENTS_V2: "11DC563F22746EE63573C93B3A63CF0140D3617368709AA0FE26131F7A1B29F2",
    PromptId.GENERATE_USER_STORIES_V1: "0A417538728418899800C01E9032935B0A672D313D7A37475E644B1AF9258827",
    PromptId.GENERATE_USER_STORIES_V2: "F0F41E8F1D7C22DA64A3D2E2C837213B1FAD2C7002A1005C4F0063FFC2FF3C8E",
    PromptId.INGEST_RELEVANCE_V1: "1990EA6DF9D3BC1576B32952B9C70A39579645A11DEFB8DE379D0D7384FD46E5",
    PromptId.SUMMARIZE_STRUCTURED_V1: "737A1F35A75E058EAB1F7D826EA400B9FB099935F8F907382EDE3CEBBBD00F84",
    PromptId.DETECT_CONFLICTS_V1: "FB65B89AB65366BB4D916BA361244DBDD2161448214A30D0E68608D810C2909B",
    PromptId.REGENERATE_STORY_V1: "F8C9496AABF0610B8813AC1F66AE413B832068EBF50FBEBD9E73A7B9D4A61EA5",
    PromptId.REPAIR_STORIES_V1: "4B8FB6C3A7A8B06C0FF16D561D9B0F1702B3B8778665243F05FE80D0B1B52715",
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
