from __future__ import annotations

from pathlib import Path
from typing import List, Optional
import hashlib
import logging
import os

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_DIR = Path(__file__).resolve().parent
REPO_ROOT = BACKEND_DIR.parent

# Default search order is explicit and stable.
# - Prefer a repo-root `.env` (common for monorepos)
# - Fall back to `backend/.env` (legacy/compat)
ENV_FILE_CANDIDATES: List[Path] = [
    REPO_ROOT / ".env",
    BACKEND_DIR / ".env",
]


def _resolve_env_file() -> Path:
    override = (os.getenv("PAYTECH_ENV_FILE") or "").strip()
    if override:
        return Path(override).expanduser()

    for candidate in ENV_FILE_CANDIDATES:
        if candidate.is_file():
            return candidate

    # If nothing exists, keep a deterministic default for error messages.
    # pydantic-settings will ignore it if missing.
    return REPO_ROOT / ".env"


ENV_FILE_USED: Path = _resolve_env_file()


class Settings(BaseSettings):
    """
    Backend configuration using pydantic-settings.

    Key properties:
    - Does NOT mutate the parent shell environment (no `load_dotenv()` side effects).
    - Uses a deterministic `.env` location (with an explicit override).
    - Provides safe logging + clear startup validation.
    """

    model_config = SettingsConfigDict(
        extra="ignore",
        env_file=str(ENV_FILE_USED),
        env_file_encoding="utf-8",
    )

    # Ambiente
    ENV: str = "dev"  # dev | prod

    # Debug helpers
    # Enable with: DEBUG_STREAM=true (only affects /debug/stream route in backend/app.py)
    DEBUG_STREAM: bool = False

    # OpenAI
    OPENAI_API_KEY: SecretStr = SecretStr("")
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_EMBED_MODEL: str = "text-embedding-3-small"
    OPENAI_TIMEOUT: int = 60

    # Diretórios/DB
    PAYTECH_DATA_DIR: Optional[str] = None
    PAYTECH_DB_URL: Optional[str] = None

    # CORS
    CORS_ORIGINS: str = (
        "http://127.0.0.1:5500,http://localhost:5500,"
        "http://127.0.0.1:8000,http://localhost:8000"
    )

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

    def openai_api_key_value(self) -> str:
        try:
            return (self.OPENAI_API_KEY.get_secret_value() or "").strip()
        except Exception:
            return ""

    def openai_api_key_loaded(self) -> bool:
        return bool(self.openai_api_key_value())

    def openai_api_key_fingerprint(self) -> str:
        """
        Safe fingerprint to correlate configs without exposing the secret.
        """
        key = self.openai_api_key_value()
        if not key:
            return ""
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]

    def log_config_summary(self, logger: Optional[logging.Logger] = None) -> None:
        logger = logger or logging.getLogger("paytechai.config")
        existing = [str(p) for p in ENV_FILE_CANDIDATES if p.is_file()]
        logger.info(
            "Config loaded: ENV=%s env_file_used=%s env_files_found=%s openai_api_key_loaded=%s openai_api_key_fp=%s",
            (self.ENV or "").strip() or "dev",
            str(ENV_FILE_USED),
            existing,
            self.openai_api_key_loaded(),
            self.openai_api_key_fingerprint() if self.openai_api_key_loaded() else "",
        )


def validate_openai_settings(s: Settings) -> None:
    hint = openai_settings_hint(s)
    if hint:
        raise RuntimeError(hint)


def openai_settings_hint(s: Settings) -> str:
    """
    Returns a human-readable hint if OpenAI is not configured, otherwise "".

    Keep this side-effect free so routes/lifespan can decide whether to hard-fail
    (prod) or degrade gracefully (dev).
    """
    if s.openai_api_key_loaded():
        return ""

    existing = [str(p) for p in ENV_FILE_CANDIDATES if p.is_file()]
    hint_lines = [
        "OPENAI_API_KEY não encontrado.",
        "",
        "Onde o backend procura o arquivo .env (nesta ordem):",
        *(f"- {p}" for p in ENV_FILE_CANDIDATES),
        "",
        "Arquivo .env efetivamente configurado:",
        f"- {ENV_FILE_USED}",
        "",
        "Arquivos .env encontrados:",
        *(f"- {p}" for p in existing),
        "",
        "Correção recomendada:",
        f"- Crie/edite {REPO_ROOT / '.env'} com: OPENAI_API_KEY=sk-...",
        "- (Compat) Ou edite backend/.env com o mesmo conteúdo.",
        "",
        "Opcional (produção/CI): defina PAYTECH_ENV_FILE com o caminho absoluto do .env.",
    ]
    return "\n".join(hint_lines)


settings = Settings()
