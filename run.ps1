param(
    [int]$Port = 0,
    [switch]$Restart
)

# Lance le serveur FTMgen sur le port choisi.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if ($Port -le 0) {
    $Port = if ($env:FTM_PORT) { [int]$env:FTM_PORT } else { 8060 }
}

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Entorno Python ausente. Ejecuta primero: .\setup.cmd"
}

$listener = Get-NetTCPConnection -LocalAddress "127.0.0.1" -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($listener) {
    $existingPid = [int]$listener.OwningProcess
    $isFtmgen = $false
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2
        $isFtmgen = $health.status -eq "ok"
    }
    catch {
        $isFtmgen = $false
    }

    if (-not $isFtmgen) {
        throw "El puerto $Port esta ocupado por otro proceso (PID $existingPid)."
    }
    if (-not $Restart) {
        Write-Host "FTMgen ya esta disponible en http://127.0.0.1:$Port (PID $existingPid)"
        Write-Host "Para reiniciarlo con la version actual: .\run.cmd -Restart"
        exit 0
    }

    Write-Host "Reiniciando FTMgen (PID $existingPid)..."
    Stop-Process -Id $existingPid -Force
    for ($attempt = 0; $attempt -lt 25; $attempt++) {
        $stillListening = Get-NetTCPConnection -LocalAddress "127.0.0.1" -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        if (-not $stillListening) { break }
        Start-Sleep -Milliseconds 200
    }
    if (Get-NetTCPConnection -LocalAddress "127.0.0.1" -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
        throw "No se pudo liberar el puerto $Port despues de detener FTMgen."
    }
}

Write-Host "FTMgen disponible en http://127.0.0.1:$Port"
Write-Host "Para detener el servidor: Ctrl+C"
& $python -m uvicorn app.main:app --host 127.0.0.1 --port $Port
