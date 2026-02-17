$ErrorActionPreference = "Stop"

$backendDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $backendDir
Set-Location -LiteralPath $repoRoot

python -m venv backend\.venv
.\backend\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt

# Configure ambiente (não vai para o Git)
Copy-Item backend\.env.example backend\.env -ErrorAction SilentlyContinue

$uvicornArgs = @(
  "-m", "uvicorn",
  "backend.main:app",
  "--reload",
  "--host", "127.0.0.1",
  "--port", "8000"
)
python @uvicornArgs
