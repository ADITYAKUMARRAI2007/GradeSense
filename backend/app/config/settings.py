"""
Configuration settings for GradeSense.
"""

import os
from typing import Optional

class Settings:
    """Application settings loaded from environment."""
    
    # Database
    MONGODB_URL: str = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
    DATABASE_NAME: str = "gradesense"
    
    # API Keys
    GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
    EMERGENT_LLM_KEY: Optional[str] = os.environ.get("EMERGENT_LLM_KEY")
    LLM_API_KEY: str = GEMINI_API_KEY or EMERGENT_LLM_KEY or ""
    
    # Google OAuth
    GOOGLE_CLIENT_ID: str = os.environ.get("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET: str = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    
    # Server
    PORT: int = int(os.environ.get("PORT", 8001))
    HOST: str = os.environ.get("HOST", "0.0.0.0")
    DEBUG: bool = os.environ.get("DEBUG", "False").lower() == "true"
    
    # AI Configuration
    LLM_TIMEOUT: int = 120  # seconds
    LLM_TEMPERATURE: float = 0.0  # Deterministic grading
    
    # Processing
    PDF_ZOOM: float = 2.0  # Better quality
    JPEG_QUALITY: int = 85  # Balance quality/size
    CHUNK_SIZE: int = 8  # Pages per AI call
    MAX_WORKERS: int = 5  # Concurrent grading tasks
    
    # Cache
    CACHE_TTL_DAYS: int = 30  # Days before cache expires
    
    # File upload
    MAX_FILE_SIZE_MB: int = 100
    ALLOWED_EXTENSIONS: list = ["pdf", "png", "jpg", "jpeg"]
    
    # Logging
    LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")
    
    def validate(self):
        """Validate critical settings."""
        if not self.MONGODB_URL:
            raise ValueError("MONGODB_URI environment variable not set")
        if not self.LLM_API_KEY:
            raise ValueError("GEMINI_API_KEY or EMERGENT_LLM_KEY environment variable not set")
        return True


# Global settings instance
settings = Settings()
