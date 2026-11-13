# build.ps1 -- PowerLens 1.1 手工构建（不依赖 Gradle）
# 用法: powershell -ExecutionPolicy Bypass -File build.ps1
# 产物: D:\CODEX\w\powerlens\app2\out\PowerLens-1.1.apk

$ErrorActionPreference = 'Stop'

$JAVA_HOME  = 'D:\CODEX\tools\jdk17'
$SDK        = 'D:\CODEX\tools\android-sdk'
$BT         = Join-Path $SDK 'build-tools\35.0.0'
$PLATFORM   = Join-Path $SDK 'platforms\android-35\android.jar'

$env:JAVA_HOME = $JAVA_HOME
$env:PATH      = "$JAVA_HOME\bin;$BT;$env:PATH"
$env:TMP       = 'D:\CODEX\_pl_analysis\tmp'
$env:TEMP      = 'D:\CODEX\_pl_analysis\tmp'

$Proj   = 'D:\CODEX\DEEPSEEK-main\projects\PowerLens\app2'
$Out    = Join-Path $Proj 'out'
$Gen    = Join-Path $Out 'gen'
$Obj    = Join-Path $Out 'obj'
$Classes= Join-Path $Out 'classes'
$DexDir = Join-Path $Out 'dex'
$Res    = Join-Path $Out '_res.zip'

foreach ($d in @($Out, $Gen, $Obj, $Classes, $DexDir)) {
    if (Test-Path $d) { Remove-Item $d -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}

$Aapt2     = Join-Path $BT 'aapt2.exe'
$D8        = Join-Path $BT 'd8.bat'
$ZipAlign  = Join-Path $BT 'zipalign.exe'
$ApkSigner = Join-Path $BT 'apksigner.bat'
$Javac     = Join-Path $JAVA_HOME 'bin\javac.exe'
$Jar       = Join-Path $JAVA_HOME 'bin\jar.exe'

foreach ($t in @($Aapt2, $D8, $ZipAlign, $ApkSigner, $Javac, $Jar, $PLATFORM)) {
    if (-not (Test-Path $t)) { throw "缺少工具: $t" }
}

Write-Host '[1/7] aapt2 compile resources' -ForegroundColor Cyan
& $Aapt2 compile --dir (Join-Path $Proj 'res') -o (Join-Path $Obj 'res.zip')
if ($LASTEXITCODE -ne 0) { throw 'aapt2 compile 失败' }

Write-Host '[2/7] aapt2 link + generate R.java' -ForegroundColor Cyan
# --no-compress 让 resources.arsc 以 STORED 写入（Android 11+ 硬性要求）
# --no-compress 的副作用是 assets 也不压缩；模块 zip 本身已压缩，无所谓。
$AssetsDir = Join-Path $Proj 'assets'
$assetArg = @()
if (Test-Path $AssetsDir) {
    $assetArg = @('-A', $AssetsDir)
    Write-Host '  assets: 打包 assets/ 目录' -ForegroundColor DarkGray
}
& $Aapt2 link `
    -o (Join-Path $Out 'base.apk') `
    -I $PLATFORM `
    --manifest (Join-Path $Proj 'AndroidManifest.xml') `
    --java $Gen `
    --min-sdk-version 24 `
    --target-sdk-version 35 `
    --no-compress `
    --auto-add-overlay `
    @assetArg `
    (Join-Path $Obj 'res.zip')
if ($LASTEXITCODE -ne 0) { throw 'aapt2 link 失败' }

Write-Host '[3/7] javac (source 8, target 8)' -ForegroundColor Cyan
$srcs = @()
$srcs += Get-ChildItem (Join-Path $Proj 'src') -Recurse -Filter *.java | ForEach-Object { $_.FullName }
$srcs += Get-ChildItem $Gen -Recurse -Filter *.java | ForEach-Object { $_.FullName }
if ($srcs.Count -eq 0) { throw '没有找到 java 源文件' }

& $Javac -source 8 -target 8 -encoding UTF-8 `
    -bootclasspath $PLATFORM `
    -classpath $PLATFORM `
    -d $Classes `
    -nowarn `
    $srcs
if ($LASTEXITCODE -ne 0) { throw 'javac 失败' }

Write-Host '[4/7] d8 -> classes.dex' -ForegroundColor Cyan
$classFiles = Get-ChildItem $Classes -Recurse -Filter *.class | ForEach-Object { $_.FullName }
& $D8 --release --min-api 24 --output $DexDir --lib $PLATFORM $classFiles
if ($LASTEXITCODE -ne 0) { throw 'd8 失败' }

Write-Host '[5/7] 把 classes.dex 注入 apk（保留 resources.arsc 的 STORED 属性）' -ForegroundColor Cyan
# 关键：不能解压-重打包（会把 arsc 变回 compressed）。
# 直接在 aapt2 产出的 base.apk 上追加/替换 classes.dex。
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$merged = Join-Path $Out 'merged.apk'
if (Test-Path $merged) { Remove-Item $merged -Force }
Copy-Item (Join-Path $Out 'base.apk') $merged -Force

$dexBytes = [System.IO.File]::ReadAllBytes((Join-Path $DexDir 'classes.dex'))
$fs = [System.IO.File]::Open($merged, [System.IO.FileMode]::Open, [System.IO.FileAccess]::ReadWrite)
$zip = New-Object System.IO.Compression.ZipArchive($fs, ([System.IO.Compression.ZipArchiveMode]::Update))
try {
    $old = $zip.GetEntry('classes.dex')
    if ($old) { $old.Delete() }
    $e = $zip.CreateEntry('classes.dex', ([System.IO.Compression.CompressionLevel]::Optimal))
    $s = $e.Open()
    $s.Write($dexBytes, 0, $dexBytes.Length)
    $s.Close()
} finally {
    $zip.Dispose()
    $fs.Close()
}

$unsigned = $merged

Write-Host '[6/7] zipalign' -ForegroundColor Cyan
$aligned = Join-Path $Out 'aligned.apk'
if (Test-Path $aligned) { Remove-Item $aligned -Force }
& $ZipAlign -f -p 4 $unsigned $aligned
if ($LASTEXITCODE -ne 0) { throw 'zipalign 失败' }

Write-Host '[7/7] 签名（debug keystore，不存在则生成）' -ForegroundColor Cyan
$ks = 'D:\CODEX\tools\powerlens-debug.keystore'
if (-not (Test-Path $ks)) {
    & (Join-Path $JAVA_HOME 'bin\keytool.exe') -genkeypair -v `
        -keystore $ks -alias powerlens -keyalg RSA -keysize 2048 -validity 10000 `
        -storepass android -keypass android `
        -dname 'CN=PowerLens, OU=Dev, O=Local, L=NA, S=NA, C=CN'
    if ($LASTEXITCODE -ne 0) { throw 'keytool 失败' }
}

$final = Join-Path $Out 'PowerLens-1.1.apk'
if (Test-Path $final) { Remove-Item $final -Force }
& $ApkSigner sign `
    --ks $ks --ks-pass pass:android --key-pass pass:android --ks-key-alias powerlens `
    --min-sdk-version 24 `
    --out $final $aligned
if ($LASTEXITCODE -ne 0) { throw 'apksigner 失败' }

& $ApkSigner verify $final
if ($LASTEXITCODE -ne 0) { throw '签名校验失败' }

Write-Host ''
Write-Host "构建完成: $final" -ForegroundColor Green
$fi = Get-Item $final
Write-Host ("  大小 {0:N0} 字节 ({1:N1} KB)" -f $fi.Length, ($fi.Length / 1KB)) -ForegroundColor Green