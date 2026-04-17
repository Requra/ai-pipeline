import os

def get_llm(model_name: str = "gemini-2.0-flash"):
    """
    Retrieve the Gemini (Google GenAI) Language Model client.
    Using gemini-1.5-flash as the standard free-tier model.
    """
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain_openai import ChatOpenAI
    
    if model_name == "gemini-2.0-flash":
        return ChatGoogleGenerativeAI(
            model="gemini-2.0-flash",
            temperature=0,
            google_api_key=os.getenv("GOOGLE_API_KEY")
        )
    elif model_name == "gpt-4o":
        return ChatOpenAI(
            model="gpt-4o",
            temperature=0,
            openai_api_key=os.getenv("OPENAI_API_KEY")
        )

