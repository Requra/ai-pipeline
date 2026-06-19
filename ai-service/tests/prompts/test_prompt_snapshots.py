import hashlib
from app.prompts.registry import PromptId, get_template_path

# Expected hashes for each prompt version.
# If you intentionally change a prompt, you must update these hashes.
EXPECTED_HASHES = {
    PromptId.CLASSIFY_REQUIREMENTS_V1: "659607DD94837A84620FFFDDBD17196129DD8A2C0D3B6FEC93F1DDE1D077DAA5",
    PromptId.EXTRACT_REQUIREMENTS_V1: "C5488794A6A9B8A9EEE881FE6CB0392354B7300E9EFF5B9412491A7C5B5DA92F",
    PromptId.GENERATE_USER_STORIES_V1: "3C04EF6B1BEAAE5A226B43F11617B5F80719FB25B8D6E65077E2128A28004A40",
    PromptId.INGEST_RELEVANCE_V1: "1990EA6DF9D3BC1576B32952B9C70A39579645A11DEFB8DE379D0D7384FD46E5",
    PromptId.SUMMARIZE_STRUCTURED_V1: "737A1F35A75E058EAB1F7D826EA400B9FB099935F8F907382EDE3CEBBBD00F84",
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
