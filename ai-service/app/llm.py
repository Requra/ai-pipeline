import os

def get_llm():
    """
    Retrieve the Gemini (Google GenAI) Language Model client.
    Using gemini-1.5-flash as the standard free-tier model.
    """
    from langchain_google_genai import ChatGoogleGenerativeAI
    
    return ChatGoogleGenerativeAI(
        model="gemini-2.0-flash",
        temperature=0,
        google_api_key=os.getenv("GOOGLE_API_KEY")
    )

