"""
OpenRouter and application settings for eiqora_v2.
Uses pydantic-settings for environment variable loading.
"""

import os
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # OpenRouter Configuration
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    SITE_URL: str = "https://github.com/eiqora"
    SITE_NAME: str = "Eiqora"
    
    # Model Selection
    DEFAULT_MODEL: str = "deepseek/deepseek-v3.2"
    FAST_MODEL: str = "deepseek/deepseek-v3.2"
    
    # LLM Parameters
    LLM_TEMPERATURE: float = 0.1  # Low temp for consistent structured output
    LLM_MAX_RETRIES: int = 2
    LLM_TIMEOUT_SECONDS: int = 60
    
    # Database (reuse data_collection config)
    DATABASE_URL: str = ""
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "finance"
    
    # Agent Configuration
    MAX_CONCURRENT_AGENTS: int = 50
    TOPDOWN_CACHE_TTL_HOURS: int = 4
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"
    
    @property
    def database_url(self) -> str:
        """Construct database URL from components if not provided."""
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )
    
    @property
    def async_database_url(self) -> str:
        """Async database URL for asyncpg."""
        url = self.database_url
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
