param(
    [string]$Python = "py"
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectDir

# 这些模块由函数内部延迟导入（app-server 桥接、桌面 IPC、额度读取），
# 显式列出来，避免将来重构后 PyInstaller 漏掉而在打包版里才报 ImportError。
$hiddenImports = @(
    "--hidden-import", "codex_appserver",
    "--hidden-import", "codex_desktop_ipc",
    "--hidden-import", "codex_resume_sources"
)

if ($Python -eq "py") {
    & py -3.11 -m pip install --upgrade "pyinstaller>=6.0"
    if ($LASTEXITCODE -ne 0) { throw "Failed to install PyInstaller." }
    & py -3.11 -m PyInstaller --noconfirm --clean --windowed --onefile `
        --name "CodexAgentConsole" @hiddenImports `
        "$ProjectDir\codex_agent_console.py"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }
} else {
    & $Python -m pip install --upgrade "pyinstaller>=6.0"
    if ($LASTEXITCODE -ne 0) { throw "Failed to install PyInstaller." }
    & $Python -m PyInstaller --noconfirm --clean --windowed --onefile `
        --name "CodexAgentConsole" @hiddenImports `
        "$ProjectDir\codex_agent_console.py"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }
}

Write-Host "Built: $ProjectDir\dist\CodexAgentConsole.exe"
