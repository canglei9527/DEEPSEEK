@echo off
setlocal
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\restart-dashboard.ps1"
if errorlevel 1 (
  echo.
  echo D-AI desktop console could not be restarted.
  pause
)

endlocal
