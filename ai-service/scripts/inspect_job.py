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
    if not rec:
        print(f"Job {job_id} not found")
        return
    print(f"Job Status: {rec.status.value}")
    print(f"Progress: {rec.progress_pct}%")
    print(f"Node: {rec.current_node}")
    print(f"Input Type: {rec.input_type}")

    chunks = await stores.chunks.get_chunks(job_id)
    print(f"\nPersisted Chunks Count: {len(chunks)}")
    for i, c in enumerate(chunks):
        print(f"Chunk {i}: chunk_id={c.chunk_id}, doc_ref={c.source_document_id}, page={c.page_number}, speaker={c.speaker}, time=({c.start_time_sec}-{c.end_time_sec})")
        print(f"  preview: {c.text[:100]}...")

    res = await stores.results.get_result(job_id)
    if res:
        print(f"\nResult status: {res.get('status')}")
        result_payload = res.get("result", {})
        reqs = result_payload.get("requirements", [])
        stories = result_payload.get("stories", [])
        quality_report = result_payload.get("quality_report", {})
        
        print(f"\nRequirements ({len(reqs)}):")
        for r in reqs:
            evidence = r.get("evidence", [])
            print(f"  - [{r.get('id')}] {r.get('title')}")
            print(f"    Evidence ({len(evidence)}): {evidence}")
            
        print(f"\nStories ({len(stories)}):")
        for s in stories:
            print(f"  - [{s.get('id')}] {s.get('title')} (req_ids: {s.get('requirement_ids')})")
            print(f"    AC count: {len(s.get('acceptance_criteria', []))}")
            for ac in s.get("acceptance_criteria", []):
                print(f"      * {ac}")
                
        print(f"\nQuality Report: passed={quality_report.get('passed')}, score={quality_report.get('overall_score')}")
        if quality_report.get("issues"):
            print(f"Quality Issues: {quality_report.get('issues')}")

if __name__ == "__main__":
    asyncio.run(inspect())
