import os
import google.generativeai as genai
from dotenv import load_dotenv

# Path to the .env in ai-service/
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

def list_models():
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("ERROR: GOOGLE_API_KEY not found in .env")
        return

    genai.configure(api_key=api_key)
    
    print(f"--- Available Gemini Models for your Key ---")
    try:
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                print(f"- {m.name}")
    except Exception as e:
        print(f"Error listing models: {e}")

if __name__ == "__main__":
    list_models()
