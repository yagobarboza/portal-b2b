# Disaster Recovery — Portal B2B

## Objetivos (seção 49)
- **RPO (Recovery Point Objective):** máximo de dados aceitável de perder.
  - Sugerido: 24h (backup diário). Ajustar conforme contrato comercial.
- **RTO (Recovery Time Objective):** tempo máximo de indisponibilidade.
  - Sugerido: 4h. Ajustar conforme contrato comercial.

## Backup (seção 48)
- Frequência: diário (automatizar via cron/agendador).
- Comando: `bash scripts/backup.sh --test` (gera e valida restauração).
- Retenção: 7 cópias (configurável via `BACKUP_RETENTION`).
- Local: `./backups/` — mover para armazenamento externo (S3/R2) em produção.
- **Regra:** um backup só é válido após o teste de restauração passar.

## Procedimento de recuperação (RTO 4h)
1. Subir infraestrutura: `docker compose up -d --build`
2. Restaurar o backup mais recente:
```bash
   ls -1t backups/portal_b2b_*.dump | head -1
   docker compose exec -T postgres pg_restore \
     -U ${POSTGRES_USER} -d ${POSTGRES_DB} \
     --no-owner --no-privileges < backups/portal_b2b_ULTIMO.dump