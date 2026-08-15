import pytest
from scripts.readiness_reporter import compute_verdict, render_markdown_report


def _sample_report_data(golden_status: str = "COMPLETED", all_pass: bool = True, fallback_blocked: bool = True) -> dict:
    return {
        "metadata": {
            "timestamp": "2026-08-15T06:00:00Z",
            "branch": "feat/doc-audio-processing",
            "commit": "e50b215",
            "python_version": "3.14.2",
            "llm_provider": "groq",
            "llm_model": "llama-3.3-70b-versatile",
            "stt_provider": "groq",
            "stt_model": "whisper-large-v3",
            "database": "PostgreSQL 16 + pgvector (Neon)",
            "real_provider_execution_confirmed": True,
        },
        "matrix": [
            {
                "id": "GOLDEN_E2E (EC2)",
                "scenario": "Mixed 4 sources",
                "expected": "COMPLETED",
                "actual": f"Status={golden_status}",
                "status": "PASS" if all_pass and golden_status == "COMPLETED" else "FAIL",
                "duration": "145.20s",
            },
            {
                "id": "EC16",
                "scenario": "MIME spoofing",
                "expected": "415",
                "actual": "415" if all_pass else "202",
                "status": "PASS" if all_pass else "FAIL",
                "duration": "0.10s",
            },
        ],
        "golden_e2e": {
            "job_id": "golden-12345",
            "status": golden_status,
            "total_time_seconds": 145.20,
            "requirements_count": 10,
            "stories_count": 10,
            "source_documents_count": 4,
            "persisted_chunks_count": 4,
            "quality_report": {
                "overall_score": 0.9369,
                "groundedness_score": 0.8729,
                "traceability_coverage": 0.9000,
                "story_completeness": 1.0,
            },
        },
        "performance_timings": {
            "total_e2e_seconds": 145.20,
            "source_prep_seconds": 2.15,
            "stt_seconds": 1.84,
            "index_build_seconds": 0.42,
            "extraction_seconds": 18.30,
            "retrieval_seconds": 4.10,
            "generation_seconds": 26.80,
            "quality_seconds": 21.40,
            "summarization_seconds": 14.50,
            "persistence_seconds": 0.85,
        },
        "source_metrics": [
            {"source": "requirements.pdf", "type": "PDF", "bytes": 1353, "process_time_s": 0.12, "chunks": 1, "status": "READY"},
            {"source": "meeting-audio.mp3", "type": "Audio (MP3)", "bytes": 607338, "process_time_s": 1.84, "chunks": 1, "status": "READY"},
        ],
        "provenance_audit": {
            "summary": "Zero provenance contamination was observed:",
            "contamination_count": 0,
        },
        "stt_evaluation": {
            "provider": "groq",
            "model": "whisper-large-v3",
            "audio_duration_seconds": 36.96,
            "stt_latency_seconds": 1.84,
            "wer": 0.0633,
            "fallback_status": "BLOCKED — SECOND PROVIDER (DEEPGRAM) NOT CONFIGURED IN ENVIRONMENT" if fallback_blocked else "AVAILABLE",
        },
        "concurrency_benchmarks": [
            {"concurrency": 1, "total_jobs": 1, "succeeded": 1, "total_wall_seconds": 12.5, "mean_e2e_seconds": 12.5, "errors_or_429s": 0},
            {"concurrency": 2, "total_jobs": 2, "succeeded": 2, "total_wall_seconds": 24.1, "mean_e2e_seconds": 12.05, "errors_or_429s": 0},
            {"concurrency": 3, "total_jobs": 3, "succeeded": 3, "total_wall_seconds": 38.6, "mean_e2e_seconds": 12.87, "errors_or_429s": 0},
        ],
    }


def test_verdict_engine_when_blocker_exists():
    """If any test in matrix fails or Golden ends in PARTIAL, verdict must be DO NOT SHIP."""
    # Failed test case
    data_failed = _sample_report_data(golden_status="COMPLETED", all_pass=False)
    verdict, rec, bugs, blockers = compute_verdict(data_failed)
    assert "DO NOT SHIP" in verdict
    assert rec == "DO NOT SHIP"
    assert len(blockers) > 0

    # Partial golden job
    data_partial = _sample_report_data(golden_status="PARTIAL", all_pass=True)
    verdict, rec, bugs, blockers = compute_verdict(data_partial)
    assert "DO NOT SHIP" in verdict
    assert rec == "DO NOT SHIP"
    assert len(blockers) > 0


def test_verdict_engine_when_ready_with_conditions():
    """If all tests pass but fallback STT is blocked, verdict must be SHIP WITH CONDITIONS."""
    data = _sample_report_data(golden_status="COMPLETED", all_pass=True, fallback_blocked=True)
    verdict, rec, bugs, blockers = compute_verdict(data)
    assert verdict == "PRODUCTION READY WITH CONDITIONS"
    assert rec == "SHIP WITH CONDITIONS"
    assert len(blockers) == 0


def test_verdict_engine_full_ship():
    """If all tests pass and all providers available, verdict must be SHIP."""
    data = _sample_report_data(golden_status="COMPLETED", all_pass=True, fallback_blocked=False)
    verdict, rec, bugs, blockers = compute_verdict(data)
    assert verdict == "PRODUCTION READY"
    assert rec == "SHIP"
    assert len(blockers) == 0


def test_markdown_report_rendering_strict_parity():
    """Ensure rendered Markdown strictly reflects numbers from the JSON object without hardcoding."""
    data = _sample_report_data(golden_status="COMPLETED", all_pass=True, fallback_blocked=True)
    md = render_markdown_report(data)

    # 1. Check Executive Verdict & Recommendation
    assert "PRODUCTION READY WITH CONDITIONS" in md
    assert "SHIP WITH CONDITIONS" in md

    # 2. Check exact numbers from JSON
    assert "**10 user stories**" in md
    assert "**10 requirements**" in md
    assert "0.9369 (93.69%)" in md
    assert "0.8729 (87.29%)" in md
    assert "0.9000 (90.00%)" in md
    assert "145.20s" in md
    assert "6.33%" in md  # WER

    # 3. Check Concurrency row rendering
    assert "| 1 | 1/1 | 12.50s | 12.50s | 0 |" in md
    assert "| 2 | 2/2 | 12.05s | 24.10s | 0 |" in md
    assert "| 3 | 3/3 | 12.87s | 38.60s | 0 |" in md

    # 4. Check token counts are marked NOT CAPTURED instead of hallucinated
    assert "Token Counters:** NOT CAPTURED" in md
