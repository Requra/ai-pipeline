import httpx
import sys
import os

def main():
    if len(sys.argv) < 2:
        print("Usage: python verify_fixture_results.py <job_id>")
        sys.exit(1)
        
    job_id = sys.argv[1]
    token = os.getenv("AI_INTERNAL_SERVICE_TOKEN", "your-secure-internal-service-token")
    base_url = os.getenv("AI_SERVICE_BASE_URL", "http://127.0.0.1:8000")
    
    headers = {'Authorization': f'Bearer {token}'}
    url = f"{base_url}/internal/jobs/{job_id}/result"
    
    print(f"Fetching results for job {job_id} from {url}...")
    try:
        r = httpx.get(url, headers=headers)
        r.raise_for_status()
        d = r.json()
    except Exception as e:
        print(f"Error fetching result: {e}")
        sys.exit(1)

    print('\n=== REQUIREMENTS ===')
    for req in d.get("requirements", []):
        print(f"  {req['id']}: priority={req['priority']} | {req['description'][:80]}")

    print('\n=== STORIES ===')
    for st in d.get("user_stories", []):
        story_points = st.get("jira_fields", {}).get("story_points", 0)
        print(f"  {st['id']}: priority={st['priority']} (points={story_points}) | {st['title'][:80]}")

    warnings = d.get("warnings", [])
    if warnings:
        print('\n=== WARNINGS (CONFLICTS & RESOLUTIONS) ===')
        for w in warnings:
            print(f"  [{w.get('code')}]")
            print(f"  {w.get('message')}")
            print("-" * 50)

if __name__ == "__main__":
    main()
