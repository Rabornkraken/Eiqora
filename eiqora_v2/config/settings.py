"""
OpenRouter and application settings for eiqora_v2.
Uses pydantic-settings for environment variable loading.
"""

import os
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # OpenRouter LLM
    OPENROUTER_API_KEY: str = Field(default="")
    OPENROUTER_BASE_URL: str = Field(default="https://openrouter.ai/api/v1")
    OPENROUTER_MODEL: str = Field(default="openai/gpt-4o-2024-11-20")
    
    # Alpaca Trading API
    ALPACA_API_KEY: str = Field(default="")
    ALPACA_API_SECRET: str = Field(default="")
    ALPACA_BASE_URL: str = Field(default="https://paper-api.alpaca.markets")  # Paper trading by default
    ALPACA_PAPER_TRADING: bool = Field(default=True)

    SITE_URL: str = "https://github.com/eiqora"
    SITE_NAME: str = "Eiqora"
    
    # LLM Parameters
    LLM_TEMPERATURE: float = Field(default=0.7)
    DEFAULT_MODEL: str = Field(default="deepseek/deepseek-v3.2")  # Default LLM model
    LLM_MAX_TOKENS: int = Field(default=4096)
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
