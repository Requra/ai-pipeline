import asyncio
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.store.factory import get_stores

async def inspect():
    stores = get_stores()
    job_id = "e2e-prod-golden-1786718840"
    rec = await stores.jobs.get_job(job_id)
    print("=== JOB RECORD ===")
    print(f"Error Code: {rec.error_code}")
    print(f"Error Message: {rec.error_message}")
    
    res = await stores.results.get_result(job_id)
    print("\n=== RESULT RECORD ===")
    print(json.dumps(res, indent=2))

if __name__ == "__main__":
    asyncio.run(inspect())
