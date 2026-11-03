@echo off
setlocal
REM ============================================================
REM  ARID Feishu Remote-Control Worker Launcher
REM
REM  Starts the Feishu long-connection worker so you can drive
REM  DSH from Feishu chat messages (commands: "gai DIR task" for
REM  run, "zhuangtai" for status, "tingzhi" for stop), restricted
REM  to whitelisted directories.
REM
REM  Behavior:
REM    * Requires Node.js (install.bat installs it / dsh).
REM    * Requires the Feishu worker dependencies in
REM      feishu-worker\node_modules (install.bat can install them,
REM      or run `npm install` in this folder).
REM    * Auto-generates/refreshes feishu-worker\.env from the
REM      config GUI settings+credentials (config-gui\feishu-env.mjs),
REM      then starts the worker.
REM    * Keep this console window open: the long connection runs
REM      while it is open; closing the window stops the worker.
REM
REM  KEEP THIS FILE ASCII-ONLY + CRLF (cmd.exe parses in GBK).
REM ============================================================

cd /d "%~dp0"

REM --- Node check ---
where node >nul 2>nul
if errorlevel 1 (
    echo [ARID] ERROR: Node.js not found on PATH.
    echo        Run install.bat first (installs Node prerequisites / dsh).
    pause
    exit /b 1
)

REM --- Feishu worker dependencies check ---
if not exist "node_modules" (
    echo [ARID] ERROR: Feishu worker dependencies not installed.
    echo        Run install.bat (it offers to run `npm install` in this
    echo        folder), or manually run:
    echo            cd feishu-worker ^&^& npm install
    pause
    exit /b 1
)

REM --- Generate .env from config GUI settings+credentials ---
echo [ARID] Regenerating feishu-worker\.env from config ...
node "%~dp0..\config-gui\feishu-env.mjs"
if errorlevel 1 (
    echo [ARID] ERROR: could not generate .env.
    echo        Check that you configured the Feishu settings in the
    echo        config GUI (configure.bat) first.
    pause
    exit /b 1
)

echo [ARID] Starting Feishu long-connection worker ...
echo [ARID] Keep this window open; close it to stop the worker.
REM `call` so the worker's exit code is propagated to this batch.
call node_modules\.bin\tsx.cmd src\feishu\worker-main.ts
exit /b %ERRORLEVEL%
