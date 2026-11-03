@echo off
setlocal
REM ============================================================
REM  ARID Dual-Model Config GUI Launcher
REM  Double-click to start the local Node server (server.mjs) and
REM  open the browser to the dual-model configuration page.
REM
REM  Behavior (idempotent):
REM    * If Node is missing, shows an error and exits.
REM    * If the port (38765) is already in use, assumes the server
REM      is already running and just opens the browser.
REM    * Otherwise starts `node server.mjs` in its own console
REM      window (close that window to stop the server), waits
REM      briefly, then opens the browser.
REM
REM  Optional overrides:
REM    PORT         - the server port (default: 38765)
REM    DSH_SETTINGS - path to settings.yaml (default: the one under
REM                   the user's home .dsh folder)
REM
REM  KEEP THIS FILE ASCII-ONLY. cmd.exe parses batch files in the
REM  system ANSI codepage (GBK on Chinese Windows). Any non-ASCII
REM  text here (UTF-8 Chinese, full-width punctuation, wordless
REM  UTF-8 BOM, LF-only endings) corrupts parsing and makes the
REM  window flash and close on double-click.
REM ============================================================

REM The server listens on 38765 by default; an existing PORT env var overrides
REM it. Reflecting an overridden port in the URL below needs the same value in
REM both places; keep it simple: the GUI sticks to 38765.
set "PORT=38765"
set "URL=http://127.0.0.1:38765/"
set "DIR=%~dp0"

REM --- Change to the script directory first. ---
cd /d "%DIR%"
if errorlevel 1 goto :fail_cd

REM --- Check Node.js. ---
where node >nul 2>nul
if errorlevel 1 goto :fail_node

REM --- Port already in use? If so, just open the browser. ---
netstat -ano 2>nul | findstr ":38765" >nul 2>nul
if not errorlevel 1 (
    echo [ARID] Server already running at %URL%
    goto :open
)

echo [ARID] Starting ARID Dual-Model Config server...
echo [ARID] URL         : %URL%
echo [ARID] Config file : see the server console.
echo [ARID] Console    : close the "ARID Dual-Model Config" window to stop.

REM `start` is an internal command that does not reset errorlevel;
REM clear it first so a leftover code from the probe pipeline
REM cannot trip the failure check below.
ver >nul
start "ARID Dual-Model Config" cmd /k node server.mjs
if errorlevel 1 goto :fail_start

REM --- Wait 1-2 seconds for the server to start, then open. ---
ping -n 2 127.0.0.1 >nul
goto :open

:open
start "" "%URL%"
exit /b 0

:fail_cd
echo [ARID] ERROR: cannot enter directory "%DIR%".
pause
exit /b 1

:fail_node
echo [ARID] ERROR: Node.js was not found on PATH.
echo        Please install Node.js 18 or later first.
pause
exit /b 1

:fail_start
echo [ARID] ERROR: could not start the server window.
pause
exit /b 1
