import os
import sys
import time
import httpx
from dotenv import load_dotenv

env_path = os.path.abspath("c:/ITI_GP/src/ai-pipeline/ai-service/.env")
load_dotenv(env_path)

api_key = os.environ.get("ITI_API_KEY")
base_url = "http://apiaccess.iti.net.eg"

async def test_chat(client, model_id):
    url = f"{base_url}/api/v1/student/chat"
    payload = {
        "model_id": model_id,
        "messages": [
            {
                "role": "user",
                "content": "Reply with exactly OK"
            }
        ],
        "temperature": 0,
        "max_tokens": 10
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    
    print(f"\n--- Testing Chat Model: {model_id} ---")
    start_time = time.time()
    try:
        r = await client.post(url, json=payload, headers=headers)
        latency = (time.time() - start_time) * 1000
        print(f"Status Code: {r.status_code}")
        print(f"Latency: {latency:.2f} ms")
        if r.status_code == 200:
            resp_data = r.json()
            # Log keys to analyze schema without dumping full response content
            print(f"Response Keys: {list(resp_data.keys())}")
            # print output text length or status
            print(f"Response Output Text: {resp_data.get('output_text')}")
            # token usage details if present
            if "usage" in resp_data:
                print(f"Usage: {resp_data['usage']}")
            elif "token_usage" in resp_data:
                print(f"Token Usage: {resp_data['token_usage']}")
            else:
                # print entire metadata if no standard usage key is found
                print(f"Full response (sanitized text): {{k: v for k, v in resp_data.items() if k != 'output_text'}}")
        else:
            print(f"Response text: {r.text}")
    except Exception as e:
        print(f"Request failed: {e}")

async def test_embedding(client):
    url = f"{base_url}/api/v1/student/embed"
    payload = {
        "model_id": "amazon.titan-embed-text-v1",
        "texts": [
            "User can reset their password."
        ]
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    
    print(f"\n--- Testing Embedding Model: amazon.titan-embed-text-v1 ---")
    start_time = time.time()
    try:
        r = await client.post(url, json=payload, headers=headers)
        latency = (time.time() - start_time) * 1000
        print(f"Status Code: {r.status_code}")
        print(f"Latency: {latency:.2f} ms")
        if r.status_code == 200:
            resp_data = r.json()
            print(f"Response Keys: {list(resp_data.keys())}")
            
            # Let's inspect the keys and values to see how the vector is returned
            embedding_key = None
            for key in ["embedding", "embeddings", "vector", "vectors", "data"]:
                if key in resp_data:
                    embedding_key = key
                    break
                    
            if embedding_key:
                val = resp_data[embedding_key]
                print(f"Found embedding under key '{embedding_key}', type: {type(val)}")
                if isinstance(val, list):
                    # Check nested list
                    if len(val) > 0 and isinstance(val[0], list):
                        vector = val[0]
                        print(f"First vector len: {len(vector)}")
                    else:
                        vector = val
                        print(f"Vector len: {len(vector)}")
                    print(f"Vector preview (first 5 elements): {vector[:5]}")
                else:
                    print(f"Value preview: {str(val)[:200]}")
            else:
                print(f"Full response keys/values: {resp_data}")
        else:
            print(f"Response text: {r.text}")
    except Exception as e:
        print(f"Request failed: {e}")

async def main():
    async with httpx.AsyncClient() as client:
        await test_chat(client, "nvidia.nemotron-super-3-120b")
        await test_chat(client, "openai.gpt-oss-120b-1:0")
        await test_chat(client, "mistral.mistral-large-3-675b-instruct")
        await test_embedding(client)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
