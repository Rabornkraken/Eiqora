"""
API Configuration for Eiqora v2 Frontend
"""
from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    """API Settings"""

    # API
    API_TITLE: str = "Eiqora v2 API"
    API_VERSION: str = "1.0.0"
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000

    # CORS
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3000",
        "https://eiqorafrontend.z23.web.core.windows.net",
        "*",  # Allow all origins for Azure deployment
    ]

    # Database (inherited from eiqora_v2 settings)
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/finance"

    # Analysis Configuration
    MAX_CONCURRENT_ANALYSES: int = 5
    ANALYSIS_TIMEOUT_SECONDS: int = 300

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "allow"


settings = Settings()