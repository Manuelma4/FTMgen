# Ejecuta el caso de referencia y comprueba que el Excel generado sea legible.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Entorno Python ausente. Ejecuta primero: .\setup.cmd"
}

$excel = "Listing_pieces_et materiel MEDIVIE4_6.xlsx"
$pdf = "24-031 CABINET VASCULAIRE @ind N 16.06.2026.pdf"
$output = "output\validation_comparatif.xlsx"

& $python -m app.pipeline $excel $pdf -o $output
if ($LASTEXITCODE -ne 0) {
    throw "El pipeline termino con error."
}

& $python -m app.validate_output $output
if ($LASTEXITCODE -ne 0) {
    throw "El Excel generado no paso la validacion estructural."
}
