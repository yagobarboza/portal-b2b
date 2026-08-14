"""Exceções de domínio e tratamento centralizado (seções 15 e 41 do doc).

Regras de Data Leak Prevention:
- NUNCA expor stack traces, SQL, secrets, tokens ou detalhes internos.
- Em produção, toda exceção não mapeada retorna INTERNAL_ERROR genérico.
- Mensagens de erro são genéricas e consistentes (não revelam detalhes).
"""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

class AppError(Exception):
    """Erro de aplicação com código e status HTTP."""

    def __init__(self, message: str, code: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code

class NotFoundError(AppError):
    def __init__(self, message: str = "Recurso não encontrado.") -> None:
        super().__init__(message, "NOT_FOUND", 404)

class UnauthorizedError(AppError):
    def __init__(self, message: str = "Não autenticado.") -> None:
        super().__init__(message, "UNAUTHORIZED", 401)

class ForbiddenError(AppError):
    def __init__(self, message: str = "Acesso negado.") -> None:
        super().__init__(message, "FORBIDDEN", 403)

class ConflictError(AppError):
    def __init__(self, message: str = "Conflito.") -> None:
        super().__init__(message, "CONFLICT", 409)

class ValidationError(AppError):
    def __init__(self, message: str = "Dados inválidos.") -> None:
        super().__init__(message, "VALIDATION_ERROR", 422)

class ValidationFailedError(AppError):
    """Falha de validação de negócio (ex.: senha fraca, seção 9).

    Usada por app/core/security.py para rejeitar senhas que não
    atendem à política de força (seção 9).
    """

    def __init__(self, message: str = "Dados inválidos.") -> None:
        super().__init__(message, "VALIDATION_ERROR", 422)

class RateLimitedError(AppError):
    def __init__(self, message: str = "Muitas requisições. Tente novamente mais tarde.") -> None:
        super().__init__(message, "RATE_LIMITED", 429)

def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    """Resposta de erro padronizada (sem detalhes internos)."""
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )

def register_exception_handlers(app: FastAPI) -> None:
    """Registra todos os handlers de erro no app.

    Garante que nenhum detalhe interno (stack trace, SQL, secrets)
    vaze na resposta — seção 15.
    """

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        return _error_response(exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        # Mensagem genérica — não expõe os detalhes da validação em produção
        return _error_response(422, "VALIDATION_ERROR", "Dados inválidos.")

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        # Mapeia HTTP exceptions conhecidas, genéricas
        code = {
            401: "UNAUTHORIZED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            405: "METHOD_NOT_ALLOWED",
            429: "RATE_LIMITED",
        }.get(exc.status_code, "HTTP_ERROR")
        message = exc.detail if isinstance(exc.detail, str) else "Erro."
        return _error_response(exc.status_code, code, message)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        # NUNCA expõe o erro real em produção (seção 15)
        import logging
        logger = logging.getLogger("app")
        logger.error(
            "unhandled_error",
            exc_info=exc,
            extra={"path": str(request.url.path)},
        )
        return _error_response(500, "INTERNAL_ERROR", "Erro interno. Tente novamente mais tarde.")