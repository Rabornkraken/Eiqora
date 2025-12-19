from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # App Config
    APP_NAME: str = "Eiqora"
    DEBUG: bool = False
    
    # LLM Provider Keys
    OPENROUTER_API_KEY: Optional[str] = None
    
    # OpenRouter Config
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    SITE_URL: str = "https://github.com/eiqora" # For OpenRouter rankings
    SITE_NAME: str = "Eiqora"
    
    # Model Selection
    DEFAULT_MODEL: str = "deepseek/deepseek-v3.2"
    FAST_MODEL: str = "deepseek/deepseek-v3.2"
    
    # Data Provider Keys
    TAVILY_API_KEY: Optional[str] = None
    ALPHA_VANTAGE_API_KEY: Optional[str] = None
    
    # Vector DB
    CHROMA_PERSIST_DIRECTORY: str = "./chroma_db"

    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()