param([int]$Port = 0)

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

Write-Host "FTMgen disponible en http://127.0.0.1:$Port"
Write-Host "Para detener el servidor: Ctrl+C"
& $python -m uvicorn app.main:app --host 127.0.0.1 --port $Port
