from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    """Configurações centralizadas da aplicação.

    Lê do arquivo .env (e variáveis de ambiente do sistema).
    Secrets NUNCA ficam no código — apenas no .env (seção 42 do doc).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ===== APP =====
    APP_NAME: str = "Portal B2B"
    APP_ENV: str = "development"
    APP_DEBUG: bool = False
    APP_VERSION: str = "0.1.0"
    API_V1_PREFIX: str = "/api/v1"

    # ===== SECURITY =====
    SECRET_KEY: str  # obrigatório no .env — nunca ter default
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ===== COOKIES SEGUROS (seção 10) =====
    COOKIE_SECURE: bool = False  # True em produção (HTTPS)
    COOKIE_HTTPONLY: bool = True
    COOKIE_SAMESITE: str = "lax"  # lax | strict | none

    # ===== POLÍTICA DE SENHA (seção 9) =====
    PASSWORD_MIN_LENGTH: int = 12
    PASSWORD_REQUIRE_UPPER: bool = True
    PASSWORD_REQUIRE_LOWER: bool = True
    PASSWORD_REQUIRE_DIGIT: bool = True
    PASSWORD_REQUIRE_SPECIAL: bool = True

    # ===== RATE LIMITING (seção 8) =====
    LOGIN_RATE_LIMIT: str = "5/minute"  # tentativas de login por IP
    LOGIN_BLOCK_THRESHOLD: int = 5       # tentativas erradas antes de bloquear
    LOGIN_BLOCK_SECONDS: int = 300       # 5 min de bloqueio progressivo

    # ===== MFA (seção 11) =====
    MFA_ISSUER: str = "Portal B2B"

    # ===== DATABASE =====
    POSTGRES_USER: str = "portal"
    POSTGRES_PASSWORD: str = "portal_secret"
    POSTGRES_DB: str = "portal_b2b"
    POSTGRES_HOST: str = "postgres"
    POSTGRES_PORT: int = 5432
    DATABASE_URL: str = ""

    # ===== REDIS =====
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_URL: str = ""

    # ===== WEBHOOKS (seção 31) =====
    WEBHOOK_SECRET: str = ""  # segredo para assinatura HMAC — definir no .env
    WEBHOOK_RATE_LIMIT: int = 60      # eventos/minuto por integração
    WEBHOOK_IDEMPOTENCY_TTL: int = 86400  # segundos p/ proteção contra replay

    # ===== CLOUDFLARE R2 (Bloco 6, seção 18) =====
    R2_ACCOUNT_ID: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    R2_BUCKET_NAME: str = "portal-b2b"
    R2_ENDPOINT_URL: str = ""
    R2_PUBLIC_BASE_URL: str = ""
    R2_SIGNED_URL_EXPIRY: int = 900  # 15 min

    # ===== CORS (seção 67) =====
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173"

    # ===== LOGGING (seção 43) =====
    LOG_LEVEL: str = "INFO"
    DATA_RETENTION_DAYS: int = 365  # Retenção de audit logs (LGPD, seção 45)

    # ===== OBSERVABILIDADE (Bloco 13, seção 46) =====
    ENVIRONMENT: str = "development"   # development | staging | production
    SENTRY_DSN: str = ""              # se vazio, Sentry fica desligado
    SENTRY_TRACES_SAMPLE_RATE: float = 0.1

    # ===== POOL DE CONEXÕES (Bloco 14 — seção 54) =====
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_RECYCLE: int = 1800  # segundos

    # ===== CACHE CATÁLOGO (Bloco 14 — seção 53) =====
    CATALOG_CACHE_TTL: int = 60  # segundos

    @property
    def cors_origins_list(self) -> List[str]:
        """Converte a string de CORS do .env em lista."""
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def database_url(self) -> str:
        """URL do banco: usa DATABASE_URL se definido, senão monta dos componentes."""
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def redis_url(self) -> str:
        if self.REDIS_URL:
            return self.REDIS_URL
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

@lru_cache
def get_settings() -> Settings:
    """Singleton: carrega as settings uma única vez."""
    return Settings()