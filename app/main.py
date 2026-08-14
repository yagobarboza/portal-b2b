from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.http_security import add_cors, add_security_middleware
from app.core.logging import get_logger, setup_logging
from app.core.monitoring import init_sentry, setup_metrics
from app.core.rate_limit import limiter
from app.middleware.request_context import RequestContextMiddleware

# Logs estruturados desde o boot da aplicação
setup_logging()
logger = get_logger("main")
settings = get_settings()

# Sentry (Bloco 13 — seção 46): desligado se SENTRY_DSN vazio
init_sentry()

def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        debug=settings.APP_DEBUG,
        docs_url="/docs" if settings.APP_ENV != "production" else None,
        redoc_url="/redoc" if settings.APP_ENV != "production" else None,
    )

    # CORS restrito (seção 67) — via app/core/http_security.py
    add_cors(app)

    # Headers de segurança em toda resposta (seção 67)
    add_security_middleware(app)

    # Contexto de requisição (request_id + limpeza do tenant por request)
    app.add_middleware(RequestContextMiddleware)

    # Rate limiting (seções 8 e 39): estado global + handler genérico
    app.state.limiter = limiter

    @app.exception_handler(RateLimitExceeded)
    async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
        # Mensagem genérica — não revela limites internos
        return JSONResponse(
            status_code=429,
            content={
                "error": {
                    "code": "RATE_LIMITED",
                    "message": "Muitas tentativas. Tente novamente mais tarde.",
                }
            },
        )

    # Handlers centralizados de erro (sem vazar stack traces — seção 15)
    register_exception_handlers(app)

    # Rotas da API v1
    app.include_router(api_router, prefix=settings.API_V1_PREFIX)

    # Métricas Prometheus (Bloco 13 — seção 46): expõe GET /metrics
    setup_metrics(app)

    logger.info("application_started", env=settings.APP_ENV)
    return app

app = create_app()