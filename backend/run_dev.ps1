$ErrorActionPreference = "Stop"

$backendDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $backendDir
Set-Location -LiteralPath $repoRoot

$venvPython = Join-Path $repoRoot "backend\\.venv\\Scripts\\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
  # Compat: alguns setups antigos criam `backend/.venv`
  $venvPython = Join-Path $backendDir ".venv\\Scripts\\python.exe"
}
if (-not (Test-Path -LiteralPath $venvPython)) {
  throw "Python do venv não encontrado. Crie o venv e instale requirements.txt."
}

# Kill any process already listening on :8000 (common cause of 'network error')
try {
  $listenerProcIds = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction Stop | Select-Object -ExpandProperty OwningProcess -Unique
  foreach ($procId in $listenerProcIds) {
    try { Stop-Process -Id $procId -Force -ErrorAction Stop } catch { }
  }
} catch { }

Write-Host "Starting backend on http://127.0.0.1:8000 (reload enabled)..."
$uvicornArgs = @(
  "-m", "uvicorn",
  "backend.main:app",
  "--reload",
  "--host", "127.0.0.1",
  "--port", "8000"
)
& $venvPython @uvicornArgs
