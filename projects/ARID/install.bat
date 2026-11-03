@echo off
setlocal
REM ============================================================
REM  ARID Dual-Model Release Installer (one-click, idempotent)
REM
REM  KEEP THIS FILE ASCII-ONLY + CRLF. cmd.exe parses batch files
REM  in the system ANSI codepage (GBK on Chinese Windows); any
REM  non-ASCII byte or LF-only line ending corrupts the parse.
REM
REM  Steps:
REM    1. Check Node.js is installed (else help install).
REM    2. Install DeepSeek Harness globally if not present.
REM    3. Copy the 4 preset files into ~/.dsh/.agent-presets/arid-dualmodel/.
REM    4. Create a minimal settings.yaml skeleton if none exists.
REM    5. Print next steps.
REM ============================================================

REM --- working dir: wherever this bat lives ---
cd /d "%~dp0"

echo [ARID] ==============================================
echo [ARID]  ARID Dual-Model - Installing prerequisites
echo [ARID] ==============================================
echo.

REM --- 1) Node.js check ---
where node >nul 2>nul
if not errorlevel 1 (
    echo [ARID] Node.js found: 
    for /f "delims=" %%v in ('node --version') do echo        version %%v
) else (
    echo [ARID] Node.js was NOT found on PATH.
    echo [ARID] Install Node.js 18.19 or later, then re-run this script.
    echo [ARID]   Option A (winget):
    echo         winget install OpenJS.NodeJS.LTS
    echo [ARID]   Option B (manual download):
    echo         https://nodejs.org/en/download/prebuilt-installer
    echo.
    pause
    exit /b 1
)

REM --- 2) DeepSeek Harness check + install ---
where dsh >nul 2>nul
if errorlevel 1 (
    echo [ARID] DeepSeek Harness (dsh) not found; installing globally...
    echo        npm install -g @deepseek-ai/dsh
    call npm install -g @deepseek-ai/dsh
    if errorlevel 1 (
        echo [ARID] ERROR: npm install -g @deepseek-ai/dsh failed.
        echo        Check network / npm registry permissions, then re-run.
        pause
        exit /b 1
    )
    echo [ARID] dsh installed.
) else (
    echo [ARID] dsh already installed; skipping global install.
)

REM Refresh PATH so the freshly-installed dsh is visible in later steps.
set "PATH=%APPDATA%\npm;%PATH%"

REM --- 3) Copy agent presets ---
REM Honor $DSH_HOME when set (default ~/.dsh).
if defined DSH_HOME (
    set "PRESET_DST=%DSH_HOME%\.agent-presets\arid-dualmodel"
) else (
    set "PRESET_DST=%USERPROFILE%\.dsh\.agent-presets\arid-dualmodel"
)
if exist "%~dp0preset" (
    echo [ARID] Installing agent presets to %PRESET_DST%
    if not exist "%PRESET_DST%" mkdir "%PRESET_DST%"
    copy /y "%~dp0preset\preset.yml"          "%PRESET_DST%\preset.yml"            >nul 2>nul
    copy /y "%~dp0preset\agent.cordis.yml"    "%PRESET_DST%\agent.cordis.yml"      >nul 2>nul
    copy /y "%~dp0preset\arid-dualmodel-ext6.cjs" "%PRESET_DST%\arid-dualmodel-ext6.cjs" >nul 2>nul
    copy /y "%~dp0preset\arid-tavily.cjs"     "%PRESET_DST%\arid-tavily.cjs"       >nul 2>nul
    copy /y "%~dp0preset\kbtool.cjs"          "%PRESET_DST%\kbtool.cjs"            >nul 2>nul
    if not exist "%PRESET_DST%\preset.yml" (
        echo [ARID] ERROR: preset preset.yml could not be copied.
        pause
        exit /b 1
    )
    echo [ARID] presets copied (4 files).
) else (
    echo [ARID] No preset\ folder next to this bat; skipping preset install.
)

REM --- 4) settings.yaml skeleton ---
if defined DSH_HOME (
    set "CFG=%DSH_HOME%\settings.yaml"
    set "DSH_DIR=%DSH_HOME%"
) else (
    set "CFG=%USERPROFILE%\.dsh\settings.yaml"
    set "DSH_DIR=%USERPROFILE%\.dsh"
)
if exist "%CFG%" (
    echo [ARID] settings.yaml already exists; leaving it untouched.
) else (
    echo [ARID] Creating minimal settings.yaml skeleton at %CFG%
    if not exist "%DSH_DIR%" mkdir "%DSH_DIR%"
    (
        echo # ARID Dual-Model minimal settings
        echo # Configure your two models with configure.bat (the GUI writes back to this file).
        echo # You can also set the executor model here directly:
        echo #   arid-dual-model:
        echo #     executor:
        echo #       provider: opencode-go
        echo #       model: deepseek-v4-flash
        echo llm-pi-ai:
        echo   providers: {}
    ) > "%CFG%"
    echo [ARID] settings.yaml created.
)

REM --- 4.5) Optional Feishu worker dependencies ---
REM Installs @larksuiteoapi/node-sdk + tsx into feishu-worker so the
REM Feishu long-connection worker can run. Skipping is fine; you can
REM later run `npm install` inside the feishu-worker folder.
if exist "%~dp0feishu-worker" (
    if not exist "%~dp0feishu-worker\node_modules" (
        choice /C YN /N /M "[ARID] Install Feishu worker dependencies (needs network)? [Y/N]"
        if errorlevel 2 (
            echo [ARID] Skipped Feishu deps. Later run `npm install` in the feishu-worker folder.
        ) else (
            echo [ARID] Installing Feishu worker dependencies ...
            pushd "%~dp0feishu-worker"
            call npm install --no-audit --no-fund
            if errorlevel 1 (
                echo [ARID] WARNING: Feishu deps install failed. You can re-run `npm install`
                echo        inside the feishu-worker folder later.
            ) else (
                echo [ARID] Feishu worker dependencies installed.
            )
            popd
        )
    ) else (
        echo [ARID] Feishu worker dependencies already present; skipping.
        echo        If needed, re-run `npm install` in the feishu-worker folder.
    )
) else (
    echo [ARID] No feishu-worker folder found next to this bat; skipping Feishu deps.
)

echo.
echo [ARID] ==============================================
echo [ARID]  Installation complete.
echo [ARID] ==============================================
echo [ARID] Next steps:
echo [ARID]   1. Double-click  configure.bat  to configure the two models.
echo [ARID]   2. Double-click  start-arid.bat  to launch the DSH web workspace.
echo [ARID]      In a new session, pick the agent preset "ARID 2-Model Mode"
echo [ARID]      (from arid-dualmodel).
echo.
pause
