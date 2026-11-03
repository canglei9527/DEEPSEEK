@echo off
setlocal
cd /d "%~dp0"

rem The Node launcher probes 4173 and opens the actual selected port.
node scripts\launch-dashboard.mjs
if errorlevel 1 (
  echo.
  echo D-AI desktop console could not be started.
  pause
)

endlocal
