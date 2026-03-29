import os

def get_llm():
    """
    Retrieve the Gemini (Google GenAI) Language Model client.
    Using gemini-1.5-flash as the standard free-tier model.
    """
    from langchain_google_genai import ChatGoogleGenerativeAI
    
    # Using gemini-2.0-flash as the fast, modern free-tier model.
    return ChatGoogleGenerativeAI(
        model="gemini-2.5-flash-lite",
        temperature=0,
        google_api_key=os.getenv("GOOGLE_API_KEY")
    )

