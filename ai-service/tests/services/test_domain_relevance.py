import pytest
from unittest.mock import AsyncMock, patch

from app.services.source_processing.extractors import (
    _sample_representative_text,
    _conservative_deterministic_relevance,
    _run_relevance_check,
    RelevanceCheckResult,
)
from app.services.source_processing.models import SourceInput
from app.services.source_processing.document import process_document_source
from app.services.source_processing.audio import process_audio_source
from app.nodes.prepare_sources import prepare_sources_node
from app.schemas.pipeline_state import PipelineState


# ===========================================================================
# A. Domain Requirements Without Software Engineering Vocabulary
# ===========================================================================

@pytest.mark.parametrize(
    "domain,text",
    [
        (
            "Agriculture",
            "When soil moisture drops below 30%, watering should begin automatically. "
            "The farm manager must be able to stop irrigation manually. "
            "Watering should not start when rain is detected. "
            "If a valve remains open for more than ten minutes, the system should send an alert."
        ),
        (
            "Healthcare",
            "A nurse must confirm the medication before administration. "
            "If the prescribed dosage changes, the previous dosage must remain in the patient's history. "
            "The system must alert the doctor when patient vital signs exceed safe thresholds."
        ),
        (
            "Logistics",
            "Drivers cannot accept another delivery while an existing delivery is active. "
            "The dispatcher should see delayed shipments highlighted in red. "
            "When a shipment is delivered, the recipient must sign digitally."
        ),
        (
            "Retail",
            "A refund above 5000 EGP requires supervisor approval. "
            "Customers should receive an SMS confirmation after a return is accepted. "
            "Inventory counts must automatically decrease when an order is placed."
        ),
        (
            "Sports",
            "A coach should be able to compare an athlete's performance between tournaments. "
            "Athletes may view their own reports but cannot edit coach notes. "
            "If an injury is reported, training schedules must be updated accordingly."
        ),
        (
            "IoT / Smart Building",
            "When occupancy sensor detects zero movement for 15 minutes, lights must dim automatically. "
            "Building managers should be able to override thermostat schedules remotely. "
            "Emergency ventilation must trigger immediately if carbon monoxide levels exceed 50 ppm."
        ),
    ]
)
def test_deterministic_domain_requirements_accepted(domain, text):
    """Domain-specific requirements without words like 'API', 'backend', 'sprint' must be accepted."""
    result = _conservative_deterministic_relevance(text)
    assert result.is_useful is True, f"Failed for {domain}: {result.reason}"
    assert result.decision in ("relevant", "uncertain"), f"Failed for {domain}: {result.decision}"
    assert result.relevance_score >= 0.5


# ===========================================================================
# B. Clearly Irrelevant Sources
# ===========================================================================

@pytest.mark.parametrize(
    "category,text",
    [
        (
            "Recipe",
            "Chocolate Fudge Cake Recipe: Take two cups of flour, one cup of sugar, and three tablespoons of cocoa powder. "
            "Preheat the oven to 350 degrees Fahrenheit. Stir well and bake for 35 minutes until golden."
        ),
        (
            "Song Lyrics",
            "Verse 1: Woke up in the morning, looking at the sun. "
            "Chorus: Oh yeah, la la la, the day has just begun, repeat chorus. "
            "Verse 2: Dancing in the moonlight, feeling all the rhythm. Outro: La la la."
        ),
        (
            "Lorem Ipsum",
            "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore. "
            "Vestibulum pellentesque libero at elit tristique, a lacinia magna gravida."
        ),
    ]
)
def test_deterministic_clearly_irrelevant_rejected(category, text):
    """Obvious non-project material must be rejected by deterministic analysis."""
    result = _conservative_deterministic_relevance(text)
    assert result.is_useful is False, f"Failed for {category}: expected rejection but got {result}"
    assert result.decision == "irrelevant"
    assert result.relevance_score <= 0.3


# ===========================================================================
# C. Ambiguous / Weak Inputs (Fail-Open)
# ===========================================================================

def test_ambiguous_input_fails_open_to_uncertain():
    """Ambiguous or weak domain text should fail-open as uncertain rather than falsely rejecting."""
    ambiguous_text = (
        "General discussion notes regarding overall operations and quarterly timeline alignment. "
        "We reviewed several items with the team and noted general feedback on ongoing progress."
    )
    result = _conservative_deterministic_relevance(ambiguous_text)
    assert result.is_useful is True, "Ambiguous text must fail-open to avoid losing user input"
    assert result.decision == "uncertain"


# ===========================================================================
# D. Representative Multi-Span Sampling
# ===========================================================================

def test_representative_multi_span_sampling_captures_buried_requirements():
    """A long document starting with pleasantries/agenda setup must have requirements captured from mid/tail."""
    small_talk_head = "Hello everyone, welcome to the kickoff. The weather is great today. Let's do introductions. " * 30  # ~3000 chars
    operational_mid = " When soil moisture drops below 30 percent, the irrigation valve must open automatically. "
    filler_tail = "Thanks everyone for joining, we will meet again next month for follow up. " * 30  # ~2500 chars
    long_doc = small_talk_head + operational_mid + filler_tail

    sampled = _sample_representative_text(long_doc, max_chars=3000)
    assert len(sampled) <= 3500
    assert "moisture drops below 30" in sampled or "irrigation valve" in sampled

    result = _conservative_deterministic_relevance(sampled)
    assert result.is_useful is True
    assert result.decision == "relevant"


# ===========================================================================
# E. LLM Provider Failure Resilience
# ===========================================================================

@pytest.mark.asyncio
async def test_llm_failure_fails_open_without_false_rejection():
    """If LLM raises timeout, 429, 500, or returns invalid JSON, relevance fails open to conservative analysis."""
    domain_text = (
        "When soil moisture drops below 30%, watering should begin automatically. "
        "The farmer must be able to stop irrigation manually."
    )

    with patch("app.services.source_processing.extractors.get_llm") as mock_get_llm:
        mock_llm = AsyncMock()
        mock_llm.ainvoke.side_effect = TimeoutError("Groq LLM timeout")
        mock_get_llm.return_value = mock_llm

        res = await _run_relevance_check(domain_text)
        assert res.is_useful is True
        assert res.method == "fail_open_fallback"
        assert res.decision in ("relevant", "uncertain")


@pytest.mark.asyncio
async def test_llm_invalid_json_fails_open():
    """If LLM returns unparseable markdown/text, relevance falls back gracefully."""
    domain_text = "Drivers cannot accept another delivery while an existing delivery is active."

    with patch("app.services.source_processing.extractors.get_llm") as mock_get_llm:
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = "Sorry, I cannot produce valid JSON right now."
        mock_get_llm.return_value = mock_llm

        res = await _run_relevance_check(domain_text)
        assert res.is_useful is True
        assert res.method == "fail_open_fallback"


# ===========================================================================
# F. Audio Processing Quality Separation
# ===========================================================================

@pytest.mark.asyncio
async def test_audio_empty_transcript_fails_as_transcription_error():
    """An audio file resulting in zero transcription must fail as TRANSCRIBE_EMPTY_TRANSCRIPT, not irrelevance."""
    source = SourceInput(
        document_id="doc_audio_empty",
        filename="silent_audio.wav",
        file_type="audio",
        audio_format="wav",
        raw_bytes=b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x44\xac\x00\x00\x88\x58\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00",
    )

    with patch("app.services.source_processing.audio._validate_ffmpeg"), \
         patch("app.services.source_processing.audio._transcribe_groq", new_callable=AsyncMock) as mock_stt:
        mock_stt.return_value = ("", [])  # empty transcript
        processed = await process_audio_source(source, "job_test_empty_audio")
        assert processed.status == "failed"
        assert processed.error_code == "TRANSCRIBE_EMPTY_TRANSCRIPT"


@pytest.mark.asyncio
async def test_audio_domain_requirements_meeting_accepted():
    """A real domain audio meeting transcript must be accepted as ready without software jargon."""
    source = SourceInput(
        document_id="doc_aud_irrigation",
        filename="AUD-02_greenhouse_irrigation_meeting.wav",
        file_type="audio",
        audio_format="wav",
        raw_bytes=b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x44\xac\x00\x00\x88\x58\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00",
    )

    transcript_text = (
        "Speaker 1: When soil moisture drops below 30 percent, watering should begin automatically. "
        "Speaker 2: Agreed. The farm manager must be able to stop irrigation manually. "
        "Speaker 1: Watering should not start when rain is detected."
    )
    mock_utterances = [
        {"chunk_id": "aud_1", "text": "When soil moisture drops below 30 percent, watering should begin automatically.", "start_time_sec": 0.0, "end_time_sec": 5.0},
        {"chunk_id": "aud_2", "text": "Agreed. The farm manager must be able to stop irrigation manually.", "start_time_sec": 5.0, "end_time_sec": 10.0},
        {"chunk_id": "aud_3", "text": "Watering should not start when rain is detected.", "start_time_sec": 10.0, "end_time_sec": 15.0},
    ]

    with patch("app.services.source_processing.audio._validate_ffmpeg"), \
         patch("app.services.source_processing.audio._transcribe_groq", new_callable=AsyncMock) as mock_stt, \
         patch("app.services.source_processing.extractors.get_llm", return_value=None):
        mock_stt.return_value = (transcript_text, mock_utterances)
        processed = await process_audio_source(source, "job_test_irrigation")

        assert processed.status == "ready"
        assert processed.is_useful is True
        assert len(processed.chunks) >= 1
        assert "moisture drops below 30" in processed.raw_text


# ===========================================================================
# G. Multi-Source Mixed Relevance Pipeline Node
# ===========================================================================

@pytest.mark.asyncio
async def test_prepare_sources_mixed_relevance_continues_with_ready_sources():
    """In a mixed multi-source job, an irrelevant source is isolated and useful sources continue."""
    useful_doc = SourceInput(
        document_id="doc_valid_agri",
        filename="greenhouse_rules.txt",
        file_type="text",
        raw_text="The irrigation valves must open when soil moisture drops below 30%. Farmers can override watering manually.",
    )
    irrelevant_doc = SourceInput(
        document_id="doc_cake_recipe",
        filename="chocolate_cake_recipe.txt",
        file_type="text",
        raw_text="Take two cups of flour, one cup of sugar, and three tablespoons of cocoa. Preheat oven to 350 degrees Fahrenheit.",
    )

    state: PipelineState = {
        "job_id": "job_multi_test",
        "raw_inputs": [useful_doc.model_dump(), irrelevant_doc.model_dump()],
        "warnings": [],
    }

    with patch("app.services.source_processing.extractors.get_llm", return_value=None):
        result = await prepare_sources_node(state)

        assert result["status"] == "sources_prepared"
        assert result["is_useful"] is True
        assert len(result["chunks"]) >= 1
        assert result["partial_source_failure"] is False  # Rejected source does not fail the job

        # Check warnings
        warning_codes = [w.get("code") for w in result["warnings"]]
        assert "SOURCE_REJECTED_IRRELEVANT" in warning_codes


@pytest.mark.asyncio
async def test_prepare_sources_all_irrelevant_rejects_job():
    """If all submitted sources are definitively irrelevant, the job terminates as rejected."""
    recipe1 = SourceInput(
        document_id="doc_recipe1",
        filename="recipe1.txt",
        file_type="text",
        raw_text="Two cups of flour, sugar, and baking powder. Preheat oven to 350 degrees Fahrenheit.",
    )
    lyrics = SourceInput(
        document_id="doc_lyrics",
        filename="song.txt",
        file_type="text",
        raw_text="Verse 1: Sun goes down. Chorus: Oh yeah, la la la. Outro: La la la.",
    )

    state: PipelineState = {
        "job_id": "job_all_irrelevant",
        "raw_inputs": [recipe1.model_dump(), lyrics.model_dump()],
        "warnings": [],
    }

    with patch("app.services.source_processing.extractors.get_llm", return_value=None):
        result = await prepare_sources_node(state)

        assert result["status"] == "rejected"
        assert result["is_useful"] is False
        assert "DOCUMENT_REJECTED" in result["error"]
