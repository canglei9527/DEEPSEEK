@echo off
:: ARID 双模式离线打包脚本
:: 用途：在有网络的电脑上打包所有依赖，供离线环境使用

echo ========================================
echo   ARID 双模式离线打包工具
echo ========================================
echo.
echo 此脚本将打包以下内容：
echo   1. ARID 完整代码
echo   2. @deepseek-ai/dsh (npm 离线包)
echo   3. dsh-vision-router (npm 离线包)
echo   4. 所有依赖包
echo.
echo 请确保当前网络连接正常。
echo.
pause

:: 创建打包目录
set PACK_DIR=ARID-Offline-Package
set DATE_STAMP=%date:~0,4%%date:~5,2%%date:~8,2%

echo [1/6] 创建打包目录...
if exist "%PACK_DIR%" rmdir /s /q "%PACK_DIR%"
mkdir "%PACK_DIR%"
mkdir "%PACK_DIR%\npm-packages"
mkdir "%PACK_DIR%\ARID"

:: 复制 ARID 代码
echo.
echo [2/6] 复制 ARID 代码...
xcopy /E /I /Y /EXCLUDE:pack-exclude.txt . "%PACK_DIR%\ARID"
if %errorlevel% neq 0 (
    echo 提示: 如果看到"找不到 pack-exclude.txt"，这是正常的
    xcopy /E /I /Y . "%PACK_DIR%\ARID"
)

:: 下载 @deepseek-ai/dsh 离线包
echo.
echo [3/6] 下载 @deepseek-ai/dsh 离线包...
cd "%PACK_DIR%\npm-packages"
call npm pack @deepseek-ai/dsh
if %errorlevel% neq 0 (
    echo ❌ 下载 dsh 失败，请检查网络连接
    cd ..\..
    pause
    exit /b 1
)
echo ✅ dsh 离线包已下载

:: 下载 dsh-vision-router 离线包
echo.
echo [4/6] 下载 dsh-vision-router 离线包...
call npm pack dsh-vision-router
if %errorlevel% neq 0 (
    echo ❌ 下载 dsh-vision-router 失败
    cd ..\..
    pause
    exit /b 1
)
echo ✅ dsh-vision-router 离线包已下载

:: 下载所有依赖包（sharp, potrace 等）
echo.
echo [5/6] 下载视觉工具依赖包...
call npm pack sharp
call npm pack potrace
call npm pack puppeteer-core
call npm pack undici

cd ..\..

:: 复制安装脚本
echo.
echo [6/6] 生成离线安装脚本...

(
echo @echo off
echo :: ARID 双模式离线安装脚本
echo :: 适用于：无网络环境
echo.
echo echo ========================================
echo echo   ARID 双模式离线安装
echo echo ========================================
echo echo.
echo.
echo :: 检查 Node
echo echo [1/5] 检查 Node.js...
echo node --version ^> nul 2^>^&1
echo if %%errorlevel%% neq 0 ^(
echo     echo ❌ 未检测到 Node.js
echo     echo 请先安装 Node.js 后再运行此脚本
echo     pause
echo     exit /b 1
echo ^)
echo for /f "tokens=1" %%%%v in ^('node --version'^) do set NODE_VERSION=%%%%v
echo echo ✅ Node 版本: %%NODE_VERSION%%
echo.
echo :: 安装 dsh
echo echo.
echo echo [2/5] 安装 DeepSeek Harness (dsh)...
echo cd npm-packages
echo for %%%%f in ^(deepseek-ai-dsh-*.tgz^) do ^(
echo     echo 正在安装: %%%%f
echo     call npm install -g %%%%f
echo     if %%errorlevel%% equ 0 ^(
echo         echo ✅ dsh 安装成功
echo     ^) else ^(
echo         echo ❌ dsh 安装失败
echo         cd ..
echo         pause
echo         exit /b 1
echo     ^)
echo     goto :dsh_installed
echo ^)
echo :dsh_installed
echo.
echo :: 检查 Node 版本是否支持 dsh-vision-router
echo echo.
echo echo [3/5] 检查是否支持 dsh-vision-router...
echo for /f "tokens=1 delims=." %%%%v in ^("%%NODE_VERSION:~1%%"^) do set NODE_MAJOR=%%%%v
echo if %%NODE_MAJOR%% GEQ 22 ^(
echo     echo ✅ Node %%NODE_VERSION%% 支持 dsh-vision-router
echo     goto :install_vision_router
echo ^) else ^(
echo     echo ⚠ Node %%NODE_VERSION%% ^< 22
echo     echo 将使用旧版视觉方案 ^(1 个工具^)
echo     echo 建议升级到 Node 22+ 以使用 11 个视觉工具
echo     goto :skip_vision_router
echo ^)
echo.
echo :install_vision_router
echo echo.
echo echo [4/5] 安装 dsh-vision-router...
echo for %%%%f in ^(dsh-vision-router-*.tgz^) do ^(
echo     echo 正在安装: %%%%f
echo     call dsh plugin --profile web add %%%%f
echo     if %%errorlevel%% equ 0 ^(
echo         echo ✅ dsh-vision-router 安装成功 ^(11 个视觉工具^)
echo     ^) else ^(
echo         echo ⚠ dsh-vision-router 安装失败，将使用旧版方案
echo     ^)
echo     goto :vision_installed
echo ^)
echo :vision_installed
echo goto :copy_preset
echo.
echo :skip_vision_router
echo echo 跳过 dsh-vision-router 安装
echo.
echo :copy_preset
echo cd ..
echo.
echo :: 导入预设
echo echo.
echo echo [5/5] 导入双模式预设...
echo cd ARID
echo call install.bat
echo if %%errorlevel%% neq 0 ^(
echo     echo ⚠ 预设导入可能失败，请手动检查
echo ^)
echo.
echo cd tools\dual-model-gui
echo echo.
echo echo ========================================
echo echo   离线安装完成！
echo echo ========================================
echo echo.
echo echo 下一步：
echo echo   1. 运行: node server.mjs
echo echo   2. 浏览器访问: http://127.0.0.1:3090
echo echo.
echo pause
) > "%PACK_DIR%\install-offline.bat"

:: 创建 README
(
echo # ARID 双模式离线安装包
echo.
echo 打包日期: %DATE_STAMP%
echo.
echo ## 包含内容
echo.
echo 1. ARID 完整代码
echo 2. @deepseek-ai/dsh 离线包
echo 3. dsh-vision-router 离线包
echo 4. 所有依赖包
echo.
echo ## 安装步骤
echo.
echo ### 前置条件
echo - 已安装 Node.js（推荐 22+）
echo - Windows 系统
echo.
echo ### 安装流程
echo.
echo 1. 将整个文件夹复制到目标电脑
echo 2. 双击运行 `install-offline.bat`
echo 3. 等待安装完成
echo 4. 进入 `ARID\tools\dual-model-gui` 目录
echo 5. 运行 `node server.mjs`
echo 6. 浏览器访问 http://127.0.0.1:3090
echo.
echo ## 视觉功能说明
echo.
echo - Node ≥ 22: 自动安装 dsh-vision-router（11 个视觉工具）
echo - Node ^< 22: 使用旧版方案（1 个视觉工具）
echo.
echo ## 故障排查
echo.
echo ### 问题：npm 安装失败
echo **解决**：
echo ```
echo npm config set registry https://registry.npmmirror.com
echo ```
echo 然后重新运行 install-offline.bat
echo.
echo ### 问题：dsh-vision-router 安装失败
echo **解决**：这不是致命问题，系统会自动降级到旧版视觉方案
echo.
echo ## 更新包
echo.
echo 如需更新到最新版本，在有网络的电脑上重新运行 `pack-offline.bat`
) > "%PACK_DIR%\README.md"

:: 压缩打包（如果有 7z 或 PowerShell）
echo.
echo [完成] 打包到目录: %PACK_DIR%
echo.

:: 尝试使用 PowerShell 压缩
echo 正在尝试压缩为 ZIP 文件...
powershell -Command "Compress-Archive -Path '%PACK_DIR%' -DestinationPath 'ARID-Offline-%DATE_STAMP%.zip' -Force" 2>nul
if %errorlevel% equ 0 (
    echo ✅ 已压缩为: ARID-Offline-%DATE_STAMP%.zip
    echo.
    echo 可以删除 %PACK_DIR% 文件夹，只保留 ZIP 文件
) else (
    echo ⚠ 自动压缩失败，请手动压缩 %PACK_DIR% 文件夹
)

echo.
echo ========================================
echo   离线打包完成！
echo ========================================
echo.
echo 打包内容：
dir /B "%PACK_DIR%\npm-packages\*.tgz"
echo.
echo 包大小:
for /f "tokens=3" %%a in ('dir /-c "%PACK_DIR%" ^| find "个文件"') do echo 约 %%a 字节
echo.
echo 下一步：
echo   1. 将 %PACK_DIR% 文件夹或 ZIP 文件传输到目标电脑
echo   2. 解压后运行 install-offline.bat
echo.
pause
