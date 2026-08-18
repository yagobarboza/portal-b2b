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

# ---------- Estágio final ----------
FROM python:3.11.9-slim

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

# Copia o código da aplicação (agora inclui o Alembic — necessário em produção)
COPY app ./app
COPY worker ./worker
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini

# Permissões para o usuário não-root
RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

# Comando padrão (pode ser sobrescrito pelo compose para o worker)
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]