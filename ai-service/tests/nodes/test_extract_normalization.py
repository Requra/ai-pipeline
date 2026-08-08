import pytest
from app.nodes.extract import normalize_extraction_payload, ExtractionResponse
from app.schemas.items import SourceChunk

@pytest.fixture
def sample_chunk():
    return SourceChunk(
        chunk_id="test_chunk_1",
        text="The system shall allow users to register using email. The dashboard must load fast.",
        start_char=0,
        end_char=100
    )

def test_normalize_shorthand_label_key(sample_chunk):
    """Test shorthand { 'FR': '...' } shape."""
    payload = {
        "requirements": [
            { "FR": "The system shall allow users to register." },
            { "NFR": "The dashboard must load in less than 2 seconds." },
            { "Business Rule": "A customer email must be unique." }
        ]
    }
    
    normalized = normalize_extraction_payload(payload, sample_chunk)
    assert len(normalized["requirements"]) == 3
    
    req1 = normalized["requirements"][0]
    assert req1["text"] == "The system shall allow users to register."
    assert req1["candidate_labels"] == ["FR"]
    assert req1["id"] == 1
    assert len(req1["evidence"]) == 1
    assert req1["evidence"][0]["chunk_id"] == "test_chunk_1"

    req2 = normalized["requirements"][1]
    assert req2["candidate_labels"] == ["NFR"]

    req3 = normalized["requirements"][2]
    assert req3["candidate_labels"] == ["BR"] # Normalized from Business Rule

    # Validate with Pydantic
    ExtractionResponse.model_validate(normalized)

def test_normalize_direct_list(sample_chunk):
    """Test direct list input [ { 'FR': '...' } ]."""
    payload = [
        { "FR": "Requirement 1" },
        { "NFR": "Requirement 2" }
    ]
    
    normalized = normalize_extraction_payload(payload, sample_chunk)
    assert len(normalized["requirements"]) == 2
    assert normalized["requirements"][0]["candidate_labels"] == ["FR"]
    
    ExtractionResponse.model_validate(normalized)

def test_normalize_alternative_keys(sample_chunk):
    """Test alternative keys like 'items'."""
    payload = {
        "items": [
            { "text": "Item 1", "candidate_labels": ["FR"] }
        ]
    }
    
    normalized = normalize_extraction_payload(payload, sample_chunk)
    assert len(normalized["requirements"]) == 1
    assert normalized["requirements"][0]["text"] == "Item 1"
    
    ExtractionResponse.model_validate(normalized)

def test_normalize_full_schema_unchanged(sample_chunk):
    """Test correct full schema passes through (with minor enrichment if needed)."""
    payload = {
        "requirements": [
            {
                "id": 10,
                "text": "Full requirement",
                "candidate_labels": ["FR"],
                "confidence": 0.99,
                "evidence": [{"chunk_id": "manual", "quote": "Full requirement"}]
            }
        ]
    }
    
    normalized = normalize_extraction_payload(payload, sample_chunk)
    assert normalized["requirements"][0]["id"] == 10
    assert normalized["requirements"][0]["text"] == "Full requirement"
    assert normalized["requirements"][0]["evidence"][0]["chunk_id"] == "manual"
    
    ExtractionResponse.model_validate(normalized)

def test_normalize_missing_evidence_creation(sample_chunk):
    """Test that missing evidence is created using text as quote."""
    payload = {
        "requirements": [
            { "id": 1, "text": "No evidence here", "candidate_labels": ["FR"] }
        ]
    }
    
    normalized = normalize_extraction_payload(payload, sample_chunk)
    req = normalized["requirements"][0]
    assert len(req["evidence"]) == 1
    assert req["evidence"][0]["quote"] == "No evidence here"
    assert req["evidence"][0]["chunk_id"] == "test_chunk_1"


def test_audio_extraction_repairs_safe_punctuation_but_preserves_quote():
    audio_chunk = SourceChunk(
        chunk_id="audio-1",
        text="Asset scanning. During audits.",
        start_char=0,
        end_char=30,
        start_time_sec=0.0,
        end_time_sec=2.0,
        language="en",
    )
    payload = {
        "requirements": [{
            "id": 1,
            "text": "The system supports asset scanning. During audits.",
            "candidate_labels": ["FR"],
            "confidence": 0.9,
            "evidence": [{"chunk_id": "audio-1", "quote": audio_chunk.text}],
        }]
    }

    normalized = normalize_extraction_payload(payload, audio_chunk)["requirements"][0]

    assert normalized["text"].endswith("asset scanning during audits.")
    assert normalized["evidence"][0]["quote"] == audio_chunk.text


def test_audio_extraction_flags_likely_incomplete_acronym_fragment():
    audio_chunk = SourceChunk(
        chunk_id="audio-ldap",
        text="Integrate with LDAP Active.",
        start_char=0,
        end_char=27,
        start_time_sec=0.0,
        end_time_sec=2.0,
        language="en",
    )
    payload = {"requirements": [{
        "id": 1,
        "text": "The system shall integrate with LDAP Active.",
        "candidate_labels": ["FR"],
        "confidence": 0.9,
        "evidence": [{"chunk_id": "audio-ldap", "quote": audio_chunk.text}],
    }]}

    normalized = normalize_extraction_payload(payload, audio_chunk)["requirements"][0]

    assert normalized["needs_review"]
    assert "ASR_INCOMPLETE_FRAGMENT" in normalized["review_reason"]
