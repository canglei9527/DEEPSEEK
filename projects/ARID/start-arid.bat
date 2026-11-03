@echo off
setlocal
REM ============================================================
REM  ARID Workspace Launcher
REM  Double-click to start the DeepSeek Harness web workspace
REM  (`dsh web`) and open it in a standalone desktop window
REM  (Chrome/Edge app mode with an isolated profile - no browser
REM  tabs, no interference with your running browser).
REM
REM  Behavior:
REM    * If the workspace is already running, just opens the
REM      window (idempotent). `dsh web` itself crashes with
REM      EADDRINUSE when the port is taken, so we never run it
REM      twice.
REM    * Otherwise starts `dsh web` in its own console window
REM      (close that window to stop the workspace) and opens
REM      the desktop window once the workspace answers.
REM
REM  Optional overrides:
REM    ARID_WORKDIR - working directory (default: this folder)
REM    ARID_PORT    - port (default: 3080)
REM    ARID_WIN_W   - window width (default: 1400)
REM    ARID_WIN_H   - window height (default: 900)
REM
REM  KEEP THIS FILE ASCII-ONLY. cmd.exe parses batch files in
REM  the system ANSI codepage (GBK on Chinese Windows). Any
REM  non-ASCII text here (UTF-8 Chinese, full-width punctuation,
REM  UTF-8 BOM, LF-only line endings) corrupts line parsing and
REM  makes the window flash and close on double-click.
REM ============================================================

REM Default working directory: prefer "workspace" next to this script when it
REM exists (the packaged ARID layout), otherwise this folder. Override with the
REM ARID_WORKDIR environment variable.
if "%ARID_WORKDIR%"=="" (
    if exist "%~dp0workspace" (
        set "ARID_WORKDIR=%~dp0workspace"
    ) else (
        set "ARID_WORKDIR=%~dp0"
    )
)
if "%ARID_PORT%"=="" set "ARID_PORT=3080"
if "%ARID_WIN_W%"=="" set "ARID_WIN_W=1400"
if "%ARID_WIN_H%"=="" set "ARID_WIN_H=900"

set "ARID_URL=http://127.0.0.1:%ARID_PORT%/"

cd /d "%ARID_WORKDIR%"
if errorlevel 1 goto :fail_cd

REM --- Load TAVILY_API_KEY from .env into the process environment (optional). ---
REM The key value itself is ASCII-only, so a plain "key=value" parse is safe
REM even though cmd parses batches in the GBK codepage. Only set the web-search
REM provider when a non-empty TAVILY_API_KEY is present.
if exist ".env" (
    for /f "usebackq delims=" %%L in ("%ARID_WORKDIR%\.env") do (
        for /f "tokens=1,* delims==" %%A in ("%%L") do (
            if "%%A"=="TAVILY_API_KEY" if not "%%B"=="" set "TAVILY_API_KEY=%%B"
        )
    )
)
if defined TAVILY_API_KEY (
    set "DSH_WEB_SEARCH_PROVIDER=tavily"
)

where dsh >nul 2>nul
if errorlevel 1 goto :fail_dsh

REM --- Already running? The marker below is injected only by dsh web. ---
curl -s --max-time 3 "%ARID_URL%" 2>nul | findstr /c:"__DSH_BOOT__" >nul 2>nul
if not errorlevel 1 (
    echo [ARID] Workspace already running at %ARID_URL%
    goto :open
)

echo [ARID] Starting DeepSeek Harness workspace...
echo [ARID] Working dir : %ARID_WORKDIR%
echo [ARID] URL         : %ARID_URL%
echo [ARID] Console    : close the "ARID Workspace (dsh web)" window to stop.

set "PORT_ARGS="
if not "%ARID_PORT%"=="3080" set "PORT_ARGS=--port %ARID_PORT%"

REM `start` is an internal command that does not reset errorlevel;
REM clear it first so a leftover code from the probe pipeline
REM cannot trip the failure check below.
ver >nul
start "ARID Workspace (dsh web)" cmd /k dsh web %PORT_ARGS%
if errorlevel 1 goto :fail_start

REM --- Wait for the workspace to answer, then open the window. ---
REM `ping -n 2` is used as a delay: `timeout` fails when stdin is
REM redirected, which would break the loop in non-interactive use.
for /l %%i in (1,1,30) do (
    ping -n 2 127.0.0.1 >nul
    curl -s --max-time 2 "%ARID_URL%" 2>nul | findstr /c:"__DSH_BOOT__" >nul 2>nul
    if not errorlevel 1 goto :open
)

echo [ARID] WARNING: workspace did not answer within 30 seconds.
echo [ARID] Check the "ARID Workspace (dsh web)" window for errors.
pause
exit /b 1

:open
REM --- Pick a browser that supports app mode (standalone window). ---
REM Chrome is preferred: it opens the app window even while another
REM browser instance is already running in the background. Edge is
REM the fallback (last-match-wins below, so Chrome comes last).
set "BROWSER="
if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" set "BROWSER=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" set "BROWSER=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
if exist "%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe" set "BROWSER=%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "BROWSER=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "BROWSER=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" set "BROWSER=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"

if defined BROWSER (
    echo [ARID] Opening workspace window...
    "%BROWSER%" --app=%ARID_URL% --user-data-dir="%TEMP%\arid-web-%RANDOM%" --no-first-run --no-default-browser-check --window-size=%ARID_WIN_W%,%ARID_WIN_H%
) else (
    echo [ARID] No app-mode browser found; opening default browser...
    start "" "%ARID_URL%"
)
exit /b 0

:fail_cd
echo [ARID] ERROR: cannot enter directory "%ARID_WORKDIR%".
pause
exit /b 1

:fail_dsh
echo [ARID] ERROR: dsh was not found on PATH.
echo        Install DeepSeek Harness first:
echo            npm install -g @deepseek-ai/dsh
pause
exit /b 1

:fail_start
echo [ARID] ERROR: could not start the workspace window.
pause
exit /b 1
