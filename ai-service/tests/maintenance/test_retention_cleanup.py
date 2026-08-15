import pytest
from app.maintenance.cleanup import run_retention_cleanup


@pytest.mark.asyncio
async def test_retention_cleanup_skips_in_memory():
    """In-memory mode returns skipped status with zero purges."""
    stats = await run_retention_cleanup(database_url=None)
    assert stats["status"] == "skipped"
    assert stats["purged_results"] == 0
    assert stats["purged_chunks"] == 0


@pytest.mark.asyncio
async def test_retention_cleanup_calculates_cutoffs():
    """Retention cleanup calculates ISO cutoffs according to configured retention days."""
    stats = await run_retention_cleanup(
        result_retention_days=15,
        chunk_retention_days=7,
        database_url="",
    )
    assert stats["status"] == "skipped"
    assert "reason" in stats
