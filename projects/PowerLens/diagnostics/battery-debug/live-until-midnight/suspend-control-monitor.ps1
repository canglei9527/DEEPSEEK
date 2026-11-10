$ErrorActionPreference = 'Continue'
$serial = '192.168.1.89:45051'
$outDir = Join-Path (Get-Location) 'work\battery-debug\live-until-midnight'
$outPath = Join-Path $outDir 'suspend-control-samples.log'
$tz = [TimeZoneInfo]::FindSystemTimeZoneById('China Standard Time')
$endLocal = [DateTime]::SpecifyKind([DateTime]::new(2026, 11, 9, 0, 0, 0), [DateTimeKind]::Unspecified)
$endUtc = [TimeZoneInfo]::ConvertTimeToUtc($endLocal, $tz)

while ([DateTime]::UtcNow -lt $endUtc) {
    $nowLocal = [TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $tz)
    $stamp = $nowLocal.ToString('yyyy-MM-dd HH:mm:ss')
    $capture = @(& adb -s $serial shell su -c 'dumpsys suspend_control_internal --suspend_controls --kernel_suspends --wakeups' 2>&1)
    Add-Content -Path $outPath -Value "===== $stamp =====" -Encoding utf8
    Add-Content -Path $outPath -Value $capture -Encoding utf8
    Add-Content -Path $outPath -Value '' -Encoding utf8
    Start-Sleep -Seconds 300
}

$finalLocal = [TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $tz)
$capture = @(& adb -s $serial shell su -c 'dumpsys suspend_control_internal --suspend_controls --kernel_suspends --wakeups' 2>&1)
Add-Content -Path $outPath -Value "===== FINAL $($finalLocal.ToString('yyyy-MM-dd HH:mm:ss')) =====" -Encoding utf8
Add-Content -Path $outPath -Value $capture -Encoding utf8
