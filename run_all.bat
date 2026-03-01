@echo off
setlocal
set REPO=%~dp0
where /q pwsh
if %errorlevel%==0 (
  pwsh -NoProfile -ExecutionPolicy Bypass -File "%REPO%run_all.ps1"
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%REPO%run_all.ps1"
)
endlocal
