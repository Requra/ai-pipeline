"""
tests/nodes/test_transcribe.py
──────────────────────────────
Full test suite for the transcribe node.

Requires:
  - tests/fixtures/sample.mp3  (a real audio file for integration tests)
  - GROQ_API_KEY and/or DEEPGRAM_API_KEY in the environment / .env

Run all tests:
    pytest tests/nodes/test_transcribe.py -v -s

Run only the benchmark (both providers, side-by-side output):
    pytest tests/nodes/test_transcribe.py::test_benchmark_comparison -v -s
"""

import os
import time
import pytest
from unittest.mock import patch, AsyncMock
from dotenv import load_dotenv

load_dotenv()

from app.nodes.transcribe import transcribe_node, _transcribe_groq, _transcribe_deepgram


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _has_key(env_var: str) -> bool:
    val = os.getenv(env_var, "")
    return bool(val and "your_" not in val)

def _make_state(raw_bytes=None, file_type="audio"):
    return {
        "job_id": "test-job-transcribe",
        "raw_bytes": raw_bytes,
        "file_type": file_type,
        "raw_text": None,
        "error": None,
        "is_useful": True,
        "relevance_score": 1.0,
        "status": "started",
        "functional_requirements": [],
        "classified_requirements": [],
        "user_stories": [],
        "summary": None,
    }

def _load_fixture(filename: str) -> bytes | None:
    path = os.path.join(os.path.dirname(__file__), "..", "fixtures", filename)
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    return None


def _detect_format(raw_bytes: bytes) -> str:
    """Sniff the audio codec from magic bytes so we never lie to the API."""
    if raw_bytes[:4] == b"OggS":
        return "ogg"   # Ogg container — could be Opus or Vorbis; Deepgram handles both
    if raw_bytes[:4] == b"fLaC":
        return "flac"
    if raw_bytes[:4] == b"RIFF":
        return "wav"
    if raw_bytes[:3] in (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "mp3"
    return "mp3"  # safe default for Groq (accepts most formats)


def _count_arabic(text: str) -> tuple[int, int]:
    """Count total words and Arabic-script words."""
    words = text.split()
    arabic = sum(1 for w in words if any("\u0600" <= c <= "\u06ff" for c in w))
    return len(words), arabic


# ─────────────────────────────────────────────────────────────────────────────
# Unit Tests (no real API calls)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_audio_bytes():
    """Guard clause: missing raw_bytes returns TRANSCRIBE_NO_BYTES error."""
    state = _make_state(raw_bytes=None)
    result = await transcribe_node(state)

    assert result["raw_text"] is None
    assert "TRANSCRIBE_NO_BYTES" in result["error"]


@pytest.mark.asyncio
async def test_non_audio_file_type():
    """Non-audio files should be silently skipped — node returns None without crashing."""
    state = _make_state(raw_bytes=b"fake-pdf-content", file_type="pdf")
    result = await transcribe_node(state)

    assert result["raw_text"] is None
    assert result.get("error") is None  # silent skip, no error


@pytest.mark.asyncio
async def test_fallback_on_bad_groq_key(monkeypatch):
    """
    When the primary provider (Groq) raises an exception, the node should
    automatically attempt the fallback (Deepgram).
    We mock both to control the test outcome.
    """
    monkeypatch.setenv("TRANSCRIBE_PROVIDER", "groq")

    async def _bad_groq(raw_bytes, file_type):
        raise Exception("Simulated Groq outage")

    async def _good_deepgram(raw_bytes, file_type):
        return "Fallback transcript from Deepgram."

    with patch("app.nodes.transcribe._transcribe_groq", side_effect=_bad_groq), \
         patch("app.nodes.transcribe._transcribe_deepgram", side_effect=_good_deepgram):

        state = _make_state(raw_bytes=b"fake-audio-bytes")
        result = await transcribe_node(state)

    assert result["raw_text"] == "Fallback transcript from Deepgram."
    assert "TRANSCRIBE_GROQ_FAILURE" in result["error"]
    assert "Fallback to Deepgram Nova-3 succeeded" in result["error"]


@pytest.mark.asyncio
async def test_both_providers_fail(monkeypatch):
    """When both providers fail, raw_text is None and error contains both failure codes."""
    monkeypatch.setenv("TRANSCRIBE_PROVIDER", "groq")

    async def _bad(raw_bytes, file_type):
        raise Exception("Simulated total failure")

    with patch("app.nodes.transcribe._transcribe_groq", side_effect=_bad), \
         patch("app.nodes.transcribe._transcribe_deepgram", side_effect=_bad):

        state = _make_state(raw_bytes=b"fake-audio-bytes")
        result = await transcribe_node(state)

    assert result["raw_text"] is None
    assert "TRANSCRIBE_GROQ_FAILURE" in result["error"]
    assert "TRANSCRIBE_FALLBACK_FAILURE" in result["error"]


# ─────────────────────────────────────────────────────────────────────────────
# Integration Tests (real API calls — requires keys + fixtures/sample.mp3)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.skipif(not _has_key("GROQ_API_KEY"), reason="GROQ_API_KEY not set")
async def test_groq_whisper_real(sample_audio_bytes):
    """
    Real call to Groq Whisper large-v3.
    Asserts: raw_text is a non-empty string.
    """
    audio = sample_audio_bytes or _load_fixture("sample.mp3")
    if not audio or audio == b"fake-mp3-bytes":
        pytest.skip("No real audio fixture found at tests/fixtures/sample.mp3")

    fmt = _detect_format(audio)
    print(f"\n[Groq] Detected format: {fmt}")
    print("[Groq] Starting real transcription...")
    t0 = time.perf_counter()
    result = await _transcribe_groq(audio, fmt)
    elapsed = time.perf_counter() - t0

    print(f"[Groq] Finished in {elapsed:.2f}s")
    print(f"[Groq] Output ({len(result)} chars):")
    print("-" * 60)
    print(result)          # full transcript, no truncation
    print("-" * 60)

    assert isinstance(result, str)
    assert len(result.strip()) > 10, "Transcript is too short — possible empty audio or API error."


@pytest.mark.asyncio
@pytest.mark.skipif(not _has_key("DEEPGRAM_API_KEY"), reason="DEEPGRAM_API_KEY not set")
async def test_deepgram_real(sample_audio_bytes):
    """
    Real call to Deepgram Nova-3 with diarization.
    Asserts: raw_text is non-empty; checks for [Speaker N] labels
    (only present when multiple speakers are detected).
    """
    audio = sample_audio_bytes or _load_fixture("sample.mp3")
    if not audio or audio == b"fake-mp3-bytes":
        pytest.skip("No real audio fixture found at tests/fixtures/sample.mp3")

    fmt = _detect_format(audio)
    print(f"\n[Deepgram] Detected format: {fmt}")
    print("[Deepgram] Starting real transcription...")
    t0 = time.perf_counter()
    result = await _transcribe_deepgram(audio, fmt)
    elapsed = time.perf_counter() - t0

    print(f"[Deepgram] Finished in {elapsed:.2f}s")
    has_speakers = "[Speaker" in result
    print(f"[Deepgram] Speaker labels detected: {has_speakers}")
    print(f"[Deepgram] Output ({len(result)} chars):")
    print("-" * 60)
    print(result)          # full transcript, no truncation
    print("-" * 60)

    assert isinstance(result, str)
    assert len(result.strip()) > 10, "Transcript is too short — possible empty audio or API error."


# ─────────────────────────────────────────────────────────────────────────────
# Benchmark: side-by-side comparison of both providers (enhanced)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.skipif(
    not (_has_key("GROQ_API_KEY") and _has_key("DEEPGRAM_API_KEY")),
    reason="Both GROQ_API_KEY and DEEPGRAM_API_KEY must be set to run the benchmark.",
)
async def test_benchmark_comparison(sample_audio_bytes):
    """
    Enhanced benchmark: runs both providers in verbose mode on the same audio
    and prints a detailed quality comparison table with confidence metrics.

    This test never fails — it is designed as a decision-support tool.
    """
    audio = sample_audio_bytes or _load_fixture("sample.mp3")
    if not audio or audio == b"fake-mp3-bytes":
        pytest.skip("No real audio fixture found at tests/fixtures/sample.mp3")

    fmt = _detect_format(audio)
    print(f"\n[Benchmark] Audio format detected: {fmt}")
    print(f"[Benchmark] Audio size: {len(audio) / 1024 / 1024:.1f} MB")

    results = {}
    providers = [
        ("Groq Whisper large-v3", _transcribe_groq,     "groq"),
        ("Deepgram Nova-3",       _transcribe_deepgram,  "deepgram"),
    ]

    for name, fn, provider_key in providers:
        print(f"\n[Benchmark] Running {name} (verbose mode)...")
        t0 = time.perf_counter()
        try:
            result = await fn(audio, fmt, verbose=True)
            elapsed = time.perf_counter() - t0

            text = result["text"] if isinstance(result, dict) else result
            total_words, arabic_words = _count_arabic(text)

            entry = {
                "status": "[OK]",
                "latency_s": f"{elapsed:.2f}",
                "chars": len(text),
                "words": total_words,
                "arabic_words": arabic_words,
                "arabic_pct": f"{arabic_words / total_words * 100:.1f}%" if total_words > 0 else "0%",
                "has_speakers": "Yes" if "[Speaker" in text else "No",
                "arabic_detected": "Yes" if any("\u0600" <= c <= "\u06ff" for c in text) else "No",
                "sample": text[:200].replace("\n", " "),
            }

            # Provider-specific metrics
            if isinstance(result, dict):
                if provider_key == "groq":
                    entry["avg_logprob"] = f"{result.get('avg_logprob', 0):.3f}"
                    entry["no_speech_prob"] = f"{result.get('no_speech_prob', 0):.3f}"
                    entry["confidence"] = "— (segment-level only)"
                elif provider_key == "deepgram":
                    entry["confidence"] = f"{result.get('confidence', 0):.4f}"
                    entry["speaker_count"] = result.get("speaker_count", 1)
                    entry["avg_logprob"] = "—"
                    entry["no_speech_prob"] = "—"

            results[name] = entry

        except Exception as exc:
            elapsed = time.perf_counter() - t0
            results[name] = {
                "status": "[FAIL]",
                "latency_s": f"{elapsed:.2f}",
                "chars": 0,
                "words": 0,
                "arabic_words": 0,
                "arabic_pct": "—",
                "has_speakers": "—",
                "arabic_detected": "—",
                "confidence": "—",
                "avg_logprob": "—",
                "no_speech_prob": "—",
                "sample": str(exc)[:200],
            }

    # ── Print comparison table ──
    cols = [
        ("status",          "Status"),
        ("latency_s",       "Latency (s)"),
        ("chars",           "Output chars"),
        ("words",           "Word count"),
        ("arabic_words",    "Arabic words"),
        ("arabic_pct",      "Arabic %"),
        ("has_speakers",    "Speaker labels"),
        ("arabic_detected", "Arabic script"),
        ("confidence",      "Word confidence"),
        ("avg_logprob",     "Avg log-prob"),
        ("no_speech_prob",  "No-speech prob"),
    ]

    col_w = 32
    label_w = 22

    header = f"\n{'Metric':<{label_w}}" + "".join(f"{n:<{col_w}}" for n in results)
    sep = "─" * (label_w + col_w * len(results))

    print("\n" + "=" * len(sep))
    print("  TRANSCRIPTION PROVIDER BENCHMARK (Enhanced)")
    print("=" * len(sep))
    print(header)
    print(sep)
    for col_key, col_label in cols:
        row = f"{col_label:<{label_w}}" + "".join(
            f"{str(results[n].get(col_key, '—')):<{col_w}}" for n in results
        )
        print(row)
    print(sep)

    for name, data in results.items():
        print(f"\n[{name}] Sample output:\n  {data.get('sample', '—')!r}")

    print(f"\n{'─' * len(sep)}")
    print("  QUALITY GUIDE:")
    print("  • Word confidence > 0.90 = 🟢 Excellent  |  > 0.80 = 🟡 Good  |  < 0.80 = 🔴 Poor")
    print("  • Avg log-prob > -0.3 = 🟢 Excellent     |  > -0.6 = 🟡 Good  |  < -0.6 = 🔴 Poor")
    print("  • No-speech prob < 0.1 = 🟢 Low           |  < 0.3 = 🟡 Med   |  > 0.3 = 🔴 Hallucination risk")
    print(f"{'─' * len(sep)}")

    print("\n[Recommendation]")
    print("  → For Egyptian Arabic + English code-switching: compare word counts and Arabic %")
    print("  → For multi-speaker meetings needing diarization: prefer Deepgram Nova-3")
    print("  → For batch processing with free tier: prefer Groq Whisper large-v3")
    print(f"  → Set your choice in .env:  TRANSCRIBE_PROVIDER=groq  or  deepgram\n")

    # This test always passes -- it's a decision tool, not a correctness check.
    assert True
