$ErrorActionPreference = 'SilentlyContinue'
$target = '192.168.1.89:41073'
$deadline = [DateTimeOffset]::Parse('2026-11-10T23:59:59+08:00').LocalDateTime
$log = Join-Path $PSScriptRoot 'adb-live-watch.log'
$cycle = 0
$lastSummary = [DateTime]::MinValue
function Write-WatchLog([string]$message) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz') $message"
    Add-Content -LiteralPath $log -Value $line -Encoding UTF8
}
Write-WatchLog "watch_start host_only target=$target deadline=$($deadline.ToString('yyyy-MM-dd HH:mm:ss zzz'))"
while ((Get-Date) -lt $deadline) {
    # get-state consults the host adb server; it sends no shell work to the phone.
    $state = (& adb -s $target get-state 2>$null | Out-String).Trim()
    $adbExit = $LASTEXITCODE
    if ($adbExit -ne 0 -or $state -ne 'device') {
        Write-WatchLog "transport_not_device state=[$state] rc=$adbExit; attempting adb connect"
        $connect = (& adb connect $target 2>&1 | Out-String).Trim()
        $connectExit = $LASTEXITCODE
        Write-WatchLog "reconnect rc=$connectExit result=[$connect]"
    } elseif (((Get-Date) - $lastSummary).TotalMinutes -ge 5) {
        Write-WatchLog 'transport_online'
        $lastSummary = Get-Date
    }
    $cycle++
    Start-Sleep -Seconds 30
}
Write-WatchLog 'watch_stop deadline_reached'
