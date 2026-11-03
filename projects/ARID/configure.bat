@echo off
setlocal
REM ============================================================
REM  ARID Config GUI launcher
REM  Starts config-gui\server.mjs (Node, own console window) and
REM  opens a standalone browser window at
REM  http://127.0.0.1:%PORT%/ where PORT defaults to 3456 and can
REM  be overridden with the ARID_GUI_PORT environment variable.
REM
REM  Idempotent: if the server already answers, we just open the
REM  browser window.
REM
REM  KEEP THIS FILE ASCII-ONLY + CRLF.
REM ============================================================

cd /d "%~dp0"

REM --- Node check ---
where node >nul 2>nul
if errorlevel 1 (
    echo [ARID] ERROR: Node.js not found on PATH.
    echo        Install Node.js 18.19+ first. See install.bat or
    echo        https://nodejs.org/en/download/prebuilt-installer
    pause
    exit /b 1
)

set "PORT=3456"
if not "%ARID_GUI_PORT%"=="" set "PORT=%ARID_GUI_PORT%"
set "URL=http://127.0.0.1:%PORT%/"

REM --- Already running? ---
curl -s --max-time 2 "%URL%" 2>nul | findstr /c:"ARID" >nul 2>nul
if not errorlevel 1 (
    echo [ARID] Config GUI already running at %URL%
    goto :open
)

echo [ARID] Starting config server on %URL% ...
REM `start` spawns node in its own console window; close that window to stop it.
ver >nul
start "ARID Config Server" cmd /k "node config-gui\server.mjs"
if errorlevel 1 goto :fail_start

REM --- Wait for the server to answer ---
for /l %%i in (1,1,30) do (
    ping -n 2 127.0.0.1 >nul
    curl -s --max-time 2 "%URL%" 2>nul | findstr /c:"ARID" >nul 2>nul
    if not errorlevel 1 goto :open
)

echo [ARID] WARNING: config server did not answer within 30 seconds.
echo [ARID] Check the "ARID Config Server" window or config-gui\server.log
pause
exit /b 1

:open
echo [ARID] Opening config GUI...
set "BROWSER="
if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" set "BROWSER=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" set "BROWSER=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
if exist "%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe" set "BROWSER=%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "BROWSER=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "BROWSER=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" set "BROWSER=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"

if defined BROWSER (
    echo [ARID] Opening in app-mode window...
    "%BROWSER%" --app=%URL% --user-data-dir="%TEMP%\arid-config-%RANDOM%" --no-first-run --no-default-browser-check
) else (
    echo [ARID] No app-mode browser found; opening default browser...
    start "" "%URL%"
)
exit /b 0

:fail_start
echo [ARID] ERROR: could not start the config server.
pause
exit /b 1
