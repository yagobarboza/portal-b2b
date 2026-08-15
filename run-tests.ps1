# run-tests.ps1 — roda a suíte teste por teste, devagar, limpando o Redis entre cada um
$listFile = Join-Path $PSScriptRoot ".test-list.txt"

Write-Host "Garantindo API de pé..." -ForegroundColor Cyan
docker compose up -d api | Out-Null
Start-Sleep -Seconds 5

Write-Host "Coletando lista de testes..." -ForegroundColor Cyan
# Coleta a partir de /app/tests (onde está o pytest.ini)
docker compose exec -T api sh -c "cd /app/tests && pytest --collect-only -q 2>/dev/null" > $listFile
$tests = @(Get-Content $listFile | Where-Object { $_ -match '::' } | ForEach-Object { $_.Trim() })

if ($tests.Count -eq 0) {
    Write-Host "Nenhum teste encontrado. Conteudo do collect:" -ForegroundColor Red
    Get-Content $listFile | ForEach-Object { Write-Host $_ }
    exit 1
}

Write-Host "Encontrados $($tests.Count) testes. Rodando 1 por 1..." -ForegroundColor Cyan
$passed = 0; $failed = 0; $errored = 0; $skipped = 0; $i = 0

foreach ($t in $tests) {
    $i++
    Write-Host ""
    Write-Host "===== [$i/$($tests.Count)] $t =====" -ForegroundColor Yellow

    # Limpa o Redis (zera rate limit / cache / bloqueios)
    docker compose exec redis redis-cli FLUSHALL | Out-Null

    # Roda UM teste A PARTIR de /app/tests (para o pytest.ini ser lido)
    $out = docker compose exec -T api sh -c "cd /app/tests && pytest '$t' -v" 2>&1
    $out | ForEach-Object { Write-Host $_ }

    # Classifica
    if     ($out -match '\bPASSED\b')  { $passed++;  Write-Host "RESULTADO: PASSED"  -ForegroundColor Green }
    elseif ($out -match '\bSKIPPED\b') { $skipped++; Write-Host "RESULTADO: SKIPPED" -ForegroundColor DarkGray }
    elseif ($out -match '\bFAILED\b')  { $failed++;  Write-Host "RESULTADO: FAILED"  -ForegroundColor Red }
    else                               { $errored++; Write-Host "RESULTADO: ERRO"    -ForegroundColor Red }

    # Pausa para dar tempo de processar
    Start-Sleep -Seconds 3
}

Remove-Item $listFile -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "================ RESUMO ================" -ForegroundColor Cyan
Write-Host "Total: $($tests.Count) | Passou: $passed | Falhou: $failed | Erro: $errored | Pulou: $skipped"