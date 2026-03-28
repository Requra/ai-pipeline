import os
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic

def get_llm(provider: str = "openai", model_name: str = "gpt-4o"):
    """
    Factory to retrieve the appropriate Language Model client.
    """
    if provider == "openai":
        return ChatOpenAI(temperature=0, model=model_name, api_key=os.getenv("OPENAI_API_KEY"))
    elif provider == "anthropic":
        return ChatAnthropic(temperature=0, model=model_name, api_key=os.getenv("ANTHROPIC_API_KEY"))
    else:
        raise ValueError(f"Unsupported LLM provider: {provider}")
