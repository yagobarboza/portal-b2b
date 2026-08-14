#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# Bloco 13 — Backup PostgreSQL com retenção + teste de restauração
# Uso:
#   bash scripts/backup.sh          # gera backup com timestamp
#   bash scripts/backup.sh --test   # gera backup E testa restauração
# ============================================================

# Carrega credenciais do .env (se existir)
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

PG_USER="${POSTGRES_USER:-portal}"
PG_DB="${POSTGRES_DB:-portal}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION="${BACKUP_RETENTION:-7}"   # mantém os 7 backups mais recentes
STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_FILE="${BACKUP_DIR}/portal_b2b_${STAMP}.dump"

mkdir -p "$BACKUP_DIR"

echo "==> Gerando backup: ${BACKUP_FILE}"
docker compose exec -T postgres pg_dump \
  -U "${PG_USER}" \
  -d "${PG_DB}" \
  --format=custom \
  --no-owner \
  --no-privileges \
  > "$BACKUP_FILE"

chmod 600 "$BACKUP_FILE"
echo "==> Backup OK ($(du -h "$BACKUP_FILE" | cut -f1))"

# Retenção: mantém os RETENTION backups mais recentes
echo "==> Retenção: mantendo os ${RETENTION} backups mais recentes"
ls -1t "$BACKUP_DIR"/portal_b2b_*.dump 2>/dev/null | tail -n +$((RETENTION + 1)) | while read -r f; do
  echo "    Removendo antigo: $f"
  rm -f "$f"
done

if [[ "${1:-}" == "--test" ]]; then
  echo ""
  echo "==> Teste de restauração (seção 48: backup só é válido se restaurar)"
  TEST_DB="portal_restore_test"
  docker compose exec -T postgres psql -U "${PG_USER}" -d postgres \
    -c "DROP DATABASE IF EXISTS ${TEST_DB};" >/dev/null
  docker compose exec -T postgres psql -U "${PG_USER}" -d postgres \
    -c "CREATE DATABASE ${TEST_DB};" >/dev/null

  if docker compose exec -T postgres pg_restore \
      -U "${PG_USER}" \
      -d "${TEST_DB}" \
      --no-owner \
      --no-privileges \
      < "$BACKUP_FILE" 2>/dev/null; then
    TABLES=$(docker compose exec -T postgres psql -U "${PG_USER}" -d "${TEST_DB}" -tAc \
      "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")
    echo "==> Restauração OK — tabelas restauradas: ${TABLES}"
  else
    echo "==> ERRO: restauração falhou — backup INVÁLIDO" >&2
    docker compose exec -T postgres psql -U "${PG_USER}" -d postgres \
      -c "DROP DATABASE IF EXISTS ${TEST_DB};" >/dev/null
    exit 1
  fi

  docker compose exec -T postgres psql -U "${PG_USER}" -d postgres \
    -c "DROP DATABASE IF EXISTS ${TEST_DB};" >/dev/null
  echo "==> Teste de restauração concluído — backup VÁLIDO"
fi

echo "==> Backup concluído."