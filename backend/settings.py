from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent

# Carrega sempre backend/.env, sem depender do diretório de execução
load_dotenv(BASE_DIR / ".env", override=True)


class Settings(BaseSettings):
    """
    Config central do backend.
    - Mantém compatibilidade com suas envs existentes.
    - Evita CORS aberto por padrão em produção.
    """

    model_config = SettingsConfigDict(extra="ignore")

    # Ambiente
    ENV: str = "dev"  # dev | prod

    # OpenAI
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_EMBED_MODEL: str = "text-embedding-3-small"
    OPENAI_TIMEOUT: int = 60

    # Diretórios/DB
    PAYTECH_DATA_DIR: Optional[str] = None
    PAYTECH_DB_URL: Optional[str] = None

    # CORS
    CORS_ORIGINS: str = "http://127.0.0.1:5500,http://localhost:5500,http://127.0.0.1:8000,http://localhost:8000"

    # Chat / janela de contexto
    MAX_WINDOW_MESSAGES: int = 20
    MAX_TOTAL_MESSAGES: int = 80
    MAX_CHARS_PER_MESSAGE: int = 4000

    # RAG
    RAG_TOP_K: int = 6
    RAG_CHUNK_SIZE: int = 1200
    RAG_CHUNK_OVERLAP: int = 200
    KB_PREVIEW_LIMIT: int = 5000
    DOWNLOADS_CHUNK_TOKENS: int = 700
    DOWNLOADS_CHUNK_OVERLAP_TOKENS: int = 120

    # UX de respostas
    TEMPERATURE_GENERAL: float = 0.6
    TEMPERATURE_RAG: float = 0.3

    def cors_list(self) -> List[str]:
        items = [x.strip() for x in (self.CORS_ORIGINS or "").split(",")]
        return [x for x in items if x]


settings = Settings()
