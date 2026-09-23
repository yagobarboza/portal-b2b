# ---------- Estágio de build ----------
FROM python:3.11.9-slim AS builder

WORKDIR /app

# Instala dependências de build (necessárias para alguns pacotes com C)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copia e instala as dependências primeiro (aproveita cache do Docker)
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---------- Base de execução ----------
FROM python:3.11.9-slim AS runtime-base

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Instala libmagic (necessário para o python-magic validar MIME type — Bloco 6)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# Cria usuário não-root (boa prática de segurança)
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Copia as dependências instaladas do estágio builder
COPY --from=builder /install /usr/local

# Copia somente o necessario para API, workers e migration job.
COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser worker ./worker
COPY --chown=appuser:appuser alembic ./alembic
COPY --chown=appuser:appuser alembic.ini ./alembic.ini
RUN chown appuser:appuser /app

USER appuser

EXPOSE 8080

# ---------- Imagem de testes (usada pelo docker compose/CI) ----------
FROM runtime-base AS test

USER root
COPY --chown=appuser:appuser pytest.ini ./pytest.ini
COPY --chown=appuser:appuser tests_phase2 ./tests_phase2
COPY --chown=appuser:appuser tests_phase3 ./tests_phase3
COPY --chown=appuser:appuser tests_phase4 ./tests_phase4
COPY --chown=appuser:appuser tests_phase5 ./tests_phase5
COPY --chown=appuser:appuser tests_phase6 ./tests_phase6
COPY --chown=appuser:appuser tests_phase7 ./tests_phase7
USER appuser

# ---------- Imagem final de produção ----------
FROM runtime-base AS runtime

# Migrações são executadas por um Cloud Run Job dedicado. Nunca migre em
# paralelo durante o boot de cada réplica da API.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'"]
