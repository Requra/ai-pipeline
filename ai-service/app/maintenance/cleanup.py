"""
Retention cleanup command and service.
Purges aged results, chunks, and embeddings according to retention policies.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import delete, text

from app.config import settings

logger = logging.getLogger("app.maintenance.cleanup")


async def run_retention_cleanup(
    *,
    result_retention_days: Optional[int] = None,
    chunk_retention_days: Optional[int] = None,
    database_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Purge aged records from durable storage based on retention thresholds."""
    url = database_url or settings.DATABASE_URL
    if not url:
        logger.info("No DATABASE_URL configured; retention cleanup is a no-op for in-memory stores.")
        return {
            "status": "skipped",
            "reason": "in_memory_mode",
            "purged_results": 0,
            "purged_chunks": 0,
            "purged_embeddings": 0,
        }

    res_days = result_retention_days if result_retention_days is not None else settings.JOB_RESULT_RETENTION_DAYS
    chk_days = chunk_retention_days if chunk_retention_days is not None else settings.CHUNK_RETENTION_DAYS

    now = datetime.now(timezone.utc)
    res_cutoff = now - timedelta(days=res_days)
    chk_cutoff = now - timedelta(days=chk_days)

    logger.info(
        "Starting retention cleanup: result_cutoff=%s (%d days), chunk_cutoff=%s (%d days)",
        res_cutoff.isoformat(),
        res_days,
        chk_cutoff.isoformat(),
        chk_days,
    )

    from app.store.db import get_database
    from app.store.db.models import AiJobResult, AiSourceChunk, AiChunkEmbedding

    db = get_database(url)
    purged_results = 0
    purged_chunks = 0
    purged_embeddings = 0

    async with db.sessionmaker() as session:
        async with session.begin():
            # 1. Purge results older than res_cutoff
            stmt_res = delete(AiJobResult).where(AiJobResult.created_at < res_cutoff)
            r_res = await session.execute(stmt_res)
            purged_results = r_res.rowcount or 0

            # 2. Purge embeddings older than chk_cutoff
            stmt_emb = delete(AiChunkEmbedding).where(AiChunkEmbedding.created_at < chk_cutoff)
            r_emb = await session.execute(stmt_emb)
            purged_embeddings = r_emb.rowcount or 0

            # 3. Purge chunks older than chk_cutoff
            stmt_chk = delete(AiSourceChunk).where(AiSourceChunk.created_at < chk_cutoff)
            r_chk = await session.execute(stmt_chk)
            purged_chunks = r_chk.rowcount or 0

    logger.info(
        "Retention cleanup completed: purged %d results, %d chunks, %d embeddings",
        purged_results,
        purged_chunks,
        purged_embeddings,
    )

    return {
        "status": "completed",
        "purged_results": purged_results,
        "purged_chunks": purged_chunks,
        "purged_embeddings": purged_embeddings,
        "result_cutoff": res_cutoff.isoformat(),
        "chunk_cutoff": chk_cutoff.isoformat(),
    }


def main():
    """CLI entrypoint for operational maintenance jobs."""
    import sys
    logging.basicConfig(level=logging.INFO)
    try:
        stats = asyncio.run(run_retention_cleanup())
        print(f"Cleanup finished: {stats}")
    except Exception as e:
        logger.error("Retention cleanup failed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
