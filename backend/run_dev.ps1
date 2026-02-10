$ErrorActionPreference = "Stop"

$backendDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $backendDir

$venvPython = Join-Path $backendDir ".venv\\Scripts\\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
  throw "Python do venv não encontrado: $venvPython. Crie o venv e instale requirements.txt."
}

# Kill any process already listening on :8000 (common cause of 'network error')
try {
  $listenerProcIds = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction Stop | Select-Object -ExpandProperty OwningProcess -Unique
  foreach ($procId in $listenerProcIds) {
    try { Stop-Process -Id $procId -Force -ErrorAction Stop } catch { }
  }
} catch { }

Write-Host "Starting backend on http://127.0.0.1:8000 (reload enabled)..."
& $venvPython -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
