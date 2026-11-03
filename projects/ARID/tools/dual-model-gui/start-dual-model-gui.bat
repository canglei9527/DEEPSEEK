@echo off
setlocal
REM ============================================================
REM  ARID Dual-Model Execution Config GUI Launcher
REM
REM  Double-click to start (or reuse) the local Node server on
REM  127.0.0.1:3090 and open the browser to the GUI page.
REM
REM  Behavior (idempotent):
REM    * Probes http://127.0.0.1:3090/api/health with PowerShell
REM      Invoke-WebRequest (-TimeoutSec 1).
REM    * If already up -> just opens the browser.
REM    * If not -> starts  `node server.mjs` hidden via
REM      PowerShell Start-Process -WindowStyle Hidden, with the
REM      working directory set to this batch file's folder, then
REM      polls /api/health for up to 10s before opening browser.
REM
REM  Optional overrides:
REM    PORT          - server listening port (default 3090)
REM    DSH_SETTINGS  - absolute path to settings.yaml
REM    DSH_BACKUP_DIR - absolute path to the backups folder
REM
REM  KEEP THIS FILE ASCII-ONLY. cmd.exe parses batch files in the
REM  system ANSI codepage (GBK on Chinese Windows). Non-ASCII text
REM  here corrupts the file and makes the window flash/close.
REM ============================================================

set "PORT=3090"
set "URL=http://127.0.0.1:%PORT%/"
set "DIR=%~dp0"

REM --- Change to the script directory first. ---
cd /d "%DIR%"
if errorlevel 1 goto :fail_cd

REM --- Check Node.js. ---
where node >nul 2>nul
if errorlevel 1 goto :fail_node

REM --- Probe whether the server is already running. ---
pathping -n -q 1 -p 50 127.0.0.1 >nul 2>nul
for /f %%i in ('powershell -NoProfile -Command "try { $r=Invoke-WebRequest -Uri 'http://127.0.0.1:%PORT%/api/health' -TimeoutSec 1 -UseBasicParsing; if($r.StatusCode -eq 200){'1'}else{'0'} } catch { '0' }"') do set "UP=%%i"
if "%UP%"=="1" (
    echo [ARID] Server already running at %URL%
    goto :open
)

echo [ARID] Starting ARID Dual-Model Config server... (hidden)

REM Start node server.mjs hidden in this directory. Build the argument list
REM inside PowerShell so empty optional override variables are simply omitted.
set "PS=Start-Process -FilePath 'node' -ArgumentList @('server.mjs','--port','%PORT%'"
if not "%DSH_SETTINGS%"=="" set "PS=%PS%,'--settings','%DSH_SETTINGS%'"
if not "%DSH_BACKUP_DIR%"=="" set "PS=%PS%,'--backup-dir','%DSH_BACKUP_DIR%'"
set "PS=%PS%) -WorkingDirectory '%DIR%' -WindowStyle Hidden"
powershell -NoProfile -Command "%PS%"
if errorlevel 1 goto :fail_start

REM --- Wait (up to 10s) for the health endpoint to come up. ---
echo [ARID] Waiting for server to be ready...
set /a N=0
:waitloop
for /f %%i in ('powershell -NoProfile -Command "try { $r=Invoke-WebRequest -Uri 'http://127.0.0.1:%PORT%/api/health' -TimeoutSec 1 -UseBasicParsing; if($r.StatusCode -eq 200){'1'}else{'0'} } catch { '0' }"') do set "UP=%%i"
if "%UP%"=="1" goto :open
set /a N+=1
if %N% GEQ 10 (
    echo [ARID] Server did not become ready within 10s; config file may be missing or Node failed.
    pause
    exit /b 1
)
timeout /t 1 /nobreak >nul
goto :waitloop

:open
echo [ARID] Opening %URL%
start "" "%URL%"
exit /b 0

:fail_cd
echo [ARID] ERROR: could not cd to %DIR%
pause
exit /b 1

:fail_node
echo [ARID] ERROR: Node.js not found on PATH. Install Node.js 20+ first.
pause
exit /b 1

:fail_start
echo [ARID] ERROR: failed to start node server.mjs.
pause
exit /b 1
