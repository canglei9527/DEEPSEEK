param(
  [string]$ProjectRoot
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
  $ProjectRoot = Split-Path -Parent $PSScriptRoot
}
$ProjectRoot = [IO.Path]::GetFullPath([string]$ProjectRoot).TrimEnd('\')
$DashboardService = 'upstream-price-adjuster-dashboard'

function Get-ProjectDashboards {
  $listeners = Get-NetTCPConnection -State Listen -LocalAddress '127.0.0.1' -ErrorAction SilentlyContinue
  foreach ($listener in $listeners) {
    $port = [int]$listener.LocalPort
    try {
      $response = Invoke-WebRequest -UseBasicParsing -Method Get -Uri "http://127.0.0.1:$port/api/sync/status" -TimeoutSec 1
      $body = $response.Content | ConvertFrom-Json
      if ($response.StatusCode -ne 200 -or $body.service -ne $DashboardService -or $body.apiVersion -ne 1) {
        continue
      }
      [PSCustomObject]@{ Port = $port; ProcessId = [int]$listener.OwningProcess }
    } catch {
      continue
    }
  }
}

function Get-CommandLine([int]$ProcessId) {
  try {
    return (Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop).CommandLine
  } catch {
    return $null
  }
}

$dashboards = @(Get-ProjectDashboards | Sort-Object ProcessId -Unique)
foreach ($dashboard in $dashboards) {
  Write-Host "正在停止旧控制台 127.0.0.1:$($dashboard.Port) ..."
  try {
    Invoke-WebRequest -UseBasicParsing -Method Post -Uri "http://127.0.0.1:$($dashboard.Port)/api/sync/stop" -ContentType 'application/json' -Body '{}' -TimeoutSec 3 | Out-Null
  } catch {
    Write-Warning "无法通过本地 API 停止同步器，继续检查服务进程。"
  }

  $commandLine = Get-CommandLine $dashboard.ProcessId
  $normalizedCommand = if ($commandLine) { $commandLine.Replace('/', '\').ToLowerInvariant() } else { '' }
  $isNodeServer = $normalizedCommand.Contains('node') -and $normalizedCommand.Contains('server.mjs')
  if (-not $isNodeServer) {
    Write-Warning "端口 $($dashboard.Port) 的服务标识匹配，但进程不是 Node server.mjs；为避免误杀，跳过进程终止。"
    continue
  }

  Write-Host "正在关闭旧服务进程 PID $($dashboard.ProcessId) ..."
  Stop-Process -Id $dashboard.ProcessId -Force
}

Start-Sleep -Milliseconds 500
Write-Host '正在启动新控制台并打开浏览器 ...'
$launcher = Join-Path $ProjectRoot 'launch-dashboard.cmd'
$child = Start-Process -FilePath $launcher -WorkingDirectory $ProjectRoot -PassThru
$child.WaitForExit()
exit $child.ExitCode
