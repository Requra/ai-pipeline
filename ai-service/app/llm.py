from typing import Optional
from app.config import settings

def get_llm(model_name: Optional[str] = None):
    """
    Retrieve the Language Model client.
    Defaults to the model corresponding to settings.LLM_PROVIDER (or gpt-4o).
    """
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain_openai import ChatOpenAI
    
    if model_name is None:
        if settings.LLM_PROVIDER == "gemini":
            model_name = "gemini-2.5-flash"
        else:
            model_name = "gpt-4o"
    
    if model_name == "gemini-2.5-flash":
        return ChatGoogleGenerativeAI(
            model="gemini-2.5-flash",
            temperature=0,
            google_api_key=settings.GOOGLE_API_KEY
        )
    elif model_name == "gpt-4o":
        return ChatOpenAI(
            model="gpt-4o",
            temperature=0,
            openai_api_key=settings.OPENAI_API_KEY
        )
    elif model_name == "gpt-oss-120b":
        return ChatOpenAI(
            model="openai/gpt-oss-120b:free",
            temperature=0,
            api_key=settings.GPT_OSS_API_KEY,
            base_url=settings.BASE_URL_KEY
        ) # for testing only
