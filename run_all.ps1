param(
  [int]$FrontendPort = 5500
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $repoRoot

function Resolve-ShellExe {
  $pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
  if ($pwsh) { return $pwsh.Source }

  $powershell = Get-Command powershell -ErrorAction SilentlyContinue
  if ($powershell) { return $powershell.Source }

  throw "PowerShell não encontrado no PATH."
}

function Resolve-VenvPython {
  $backendDir = Join-Path $repoRoot "backend"

  $venvPython = Join-Path $repoRoot "backend\\.venv\\Scripts\\python.exe"
  if (Test-Path -LiteralPath $venvPython) { return $venvPython }

  # Compat: alguns setups antigos criam `backend/.venv`
  $venvPython = Join-Path $backendDir ".venv\\Scripts\\python.exe"
  if (Test-Path -LiteralPath $venvPython) { return $venvPython }

  throw "Python do venv não encontrado. Crie o venv e instale `backend\\requirements.txt`."
}

$shellExe = Resolve-ShellExe
$venvPython = Resolve-VenvPython

Write-Host "Iniciando backend (porta 8000) e frontend (porta $FrontendPort)..."

Start-Process -FilePath $shellExe -WorkingDirectory $repoRoot -ArgumentList @(
  "-NoProfile",
  "-ExecutionPolicy", "Bypass",
  "-File", "backend\\run_dev.ps1"
)

Start-Process -FilePath $shellExe -WorkingDirectory $repoRoot -ArgumentList @(
  "-NoProfile",
  "-ExecutionPolicy", "Bypass",
  "-Command",
  "& `"$venvPython`" -m http.server $FrontendPort --directory `"$repoRoot\\frontend`""
)

Write-Host ""
Write-Host "Backend:  http://127.0.0.1:8000/health"
Write-Host "Swagger:  http://127.0.0.1:8000/docs"
Write-Host "Frontend: http://127.0.0.1:$FrontendPort/index.html?api=http://127.0.0.1:8000"
