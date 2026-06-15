import hashlib
from app.prompts.registry import PromptId, get_template_path

# Expected hashes for each prompt version.
# If you intentionally change a prompt, you must update these hashes.
EXPECTED_HASHES = {
    PromptId.CLASSIFY_REQUIREMENTS_V1: "659607DD94837A84620FFFDDBD17196129DD8A2C0D3B6FEC93F1DDE1D077DAA5",
    PromptId.EXTRACT_REQUIREMENTS_V1: "DBAB7F7DEA4AB67C273A849D9CF1C03D2695B0164C706EB3505E43D65A2A4C17",
    PromptId.GENERATE_USER_STORIES_V1: "BE09BB28019085CD629683A5D151C60EA2149750BE9F62F8316FFCB3281E6A30",
    PromptId.INGEST_RELEVANCE_V1: "FFF7FFDE0A13F27261E3F2598A8646FB6BF38E6DAAE2E33BF9BD533B529A5F90",
    PromptId.SUMMARIZE_STRUCTURED_V1: "9EEDBE9B46C967C16F9C512EB1B47D7F52844F9362AC59AA1E53D31A93C66491",
}

def test_prompt_snapshots():
    """
    Verify that prompt templates have not changed.
    This protects against accidental edits.
    """
    for prompt_id, expected_hash in EXPECTED_HASHES.items():
        path = get_template_path(prompt_id)
        assert path.exists(), f"Prompt template missing: {path}"
        
        with open(path, "rb") as f:
            content = f.read()
            # Normalize line endings to avoid OS-specific hash failures
            # Actually, git usually handles this, but let's be safe.
            # But wait, Get-FileHash might have used raw bytes. 
            # I'll use the raw bytes for now to match Get-FileHash.
            actual_hash = hashlib.sha256(content).hexdigest().upper()
            
        assert actual_hash == expected_hash, (
            f"Prompt '{prompt_id}' has changed!\n"
            f"Expected: {expected_hash}\n"
            f"Actual:   {actual_hash}\n"
            "If this change was intentional, update the hash in test_prompt_snapshots.py "
            "after reviewing the quality of the new prompt."
        )
