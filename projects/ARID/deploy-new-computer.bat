@echo off
:: ARID 双模式一键部署脚本（新电脑使用）
:: 适用于：Windows + Git + Node 已安装的环境

echo ========================================
echo   ARID 双模式 + dsh-vision-router
echo   一键部署脚本
echo ========================================
echo.

:: 检查 Node 版本
echo [1/6] 检查 Node 版本...
node --version > nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ 未检测到 Node.js，请先安装 Node.js
    echo 下载地址: https://nodejs.org/
    pause
    exit /b 1
)

for /f "tokens=1" %%v in ('node --version') do set NODE_VERSION=%%v
echo ✅ Node 版本: %NODE_VERSION%

:: 检查 Git
echo.
echo [2/6] 检查 Git...
git --version > nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ 未检测到 Git，请先安装 Git
    pause
    exit /b 1
)
echo ✅ Git 已安装

:: Clone 或 Pull 仓库
echo.
echo [3/6] 同步代码...
if exist "ARID" (
    echo 检测到 ARID 目录已存在，执行 git pull...
    cd ARID
    git pull
    cd ..
) else (
    echo 首次部署，克隆仓库...
    echo 请输入你的 Git 仓库地址（如: https://github.com/user/ARID.git）:
    set /p REPO_URL=
    git clone %REPO_URL% ARID
)

if %errorlevel% neq 0 (
    echo ❌ Git 同步失败
    pause
    exit /b 1
)
echo ✅ 代码已同步

:: 安装全局依赖（dsh）
echo.
echo [4/6] 安装 DeepSeek Harness (dsh)...
npm install -g @deepseek-ai/dsh
if %errorlevel% neq 0 (
    echo ⚠ dsh 安装失败，将通过 GUI 安装
)

:: 安装 dsh-vision-router（Node ≥22）
echo.
echo [5/6] 安装 dsh-vision-router（视觉工具）...
for /f "tokens=1 delims=." %%v in ("%NODE_VERSION:~1%") do set NODE_MAJOR=%%v
if %NODE_MAJOR% GEQ 22 (
    echo ✅ Node %NODE_VERSION% 支持 dsh-vision-router
    dsh plugin --profile web add dsh-vision-router
    if %errorlevel% equ 0 (
        echo ✅ dsh-vision-router 安装成功（11 个视觉工具）
    ) else (
        echo ⚠ dsh-vision-router 安装失败，将使用旧版方案
    )
) else (
    echo ⚠ Node %NODE_VERSION% ^< 22，使用旧版视觉方案（1 个工具）
    echo 建议升级到 Node 22+ 以获得 11 个视觉工具
)

:: 启动 GUI
echo.
echo [6/6] 启动双模式管理台...
cd ARID\tools\dual-model-gui
echo.
echo ========================================
echo   部署完成！
echo ========================================
echo.
echo 双模式管理台启动中...
echo 浏览器将自动打开: http://127.0.0.1:3090
echo.
echo 如果未自动打开，请手动访问上述地址。
echo.
timeout /t 2 > nul

:: 启动浏览器
start http://127.0.0.1:3090

:: 启动服务器
node server.mjs

pause
