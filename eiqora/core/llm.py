from langchain_openai import ChatOpenAI
from eiqora.config.settings import settings

def get_llm(model_name: str = None, temperature: float = 0.0):
    """
    Factory to get an LLM instance based on config.
    Strictly uses OpenRouter.
    """
    if not settings.OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY is not set in environment variables.")

    model = model_name or settings.DEFAULT_MODEL
    
    return ChatOpenAI(
        model=model,
        temperature=temperature,
        api_key=settings.OPENROUTER_API_KEY,
        base_url=settings.OPENROUTER_BASE_URL,
        default_headers={
            "HTTP-Referer": settings.SITE_URL,
            "X-Title": settings.SITE_NAME,
        }
    )
