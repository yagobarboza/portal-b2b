"""Entrypoint do worker.

Placeholder do Bloco 1 — as filas/background jobs (seção 55 do doc)
entram de verdade no Bloco 11 (integrações, cronjobs, processamento async).
"""

import time

def main() -> None:
    print("[worker] Iniciado. Aguardando jobs... (placeholder do Bloco 1)")
    while True:
        time.sleep(60)

if __name__ == "__main__":
    main()