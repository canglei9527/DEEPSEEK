$ErrorActionPreference = 'Continue'
$serial = '192.168.1.89:45051'
$outDir = Join-Path (Get-Location) 'work\battery-debug\live-until-midnight'
$samplePath = Join-Path $outDir 'battery-samples.csv'
$statePath = Join-Path $outDir 'state-snapshots.csv'
$procPath = Join-Path $outDir 'process-cpu-ticks.csv'
$logPath = Join-Path $outDir 'monitor.log'
$snapshotDir = Join-Path $outDir 'snapshots'
$tz = [TimeZoneInfo]::FindSystemTimeZoneById('China Standard Time')
$endLocal = [DateTime]::SpecifyKind([DateTime]::new(2026, 11, 9, 0, 0, 0), [DateTimeKind]::Unspecified)
$endUtc = [TimeZoneInfo]::ConvertTimeToUtc($endLocal, $tz)
$detailNo = 0

if (-not (Test-Path $samplePath)) {
    'time_shanghai,charge_counter_uAh,current_now_uA,capacity_pct,status,adb_state' | Set-Content -Path $samplePath -Encoding utf8
    'time_shanghai,wakefulness,deviceidle_state,screen_on,screen_locked,jd_bucket,jd_notification_appop,aicr_bucket,aicr_record_audio' | Set-Content -Path $statePath -Encoding utf8
    'time_shanghai,package,pid,process_state,start_ticks,utime_ticks,stime_ticks' | Set-Content -Path $procPath -Encoding utf8
}

Add-Content -Path $logPath -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') starting; end=2026-11-09 00:00:00 Asia/Shanghai; cadence=30s battery, 5m details, 15m batterystats --charged" -Encoding utf8
$baselineLocal = [TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $tz)
$baselineName = $baselineLocal.ToString('yyyyMMdd-HHmm') + '-charged-baseline.txt'
& adb -s $serial shell dumpsys batterystats --charged 2>&1 |
    Set-Content -Path (Join-Path $snapshotDir $baselineName) -Encoding utf8

while ([DateTime]::UtcNow -lt $endUtc) {
    $nowLocal = [TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $tz)
    $stamp = $nowLocal.ToString('yyyy-MM-dd HH:mm:ss')
    $adbState = (& adb -s $serial get-state 2>$null | Out-String).Trim()
    if ($adbState -ne 'device') {
        $reconnect = (& adb connect $serial 2>&1 | Out-String).Trim()
        Add-Content -Path $logPath -Value "$stamp reconnect: $reconnect" -Encoding utf8
        Start-Sleep -Seconds 2
        $adbState = (& adb -s $serial get-state 2>$null | Out-String).Trim()
    }

    $batteryRead = & adb -s $serial shell su -c 'cat /sys/class/power_supply/battery/charge_counter /sys/class/power_supply/battery/current_now /sys/class/power_supply/battery/capacity /sys/class/power_supply/battery/status' 2>$null
    $batteryFields = @($batteryRead | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    while ($batteryFields.Count -lt 4) { $batteryFields += '' }
    "$stamp,$($batteryFields[0]),$($batteryFields[1]),$($batteryFields[2]),$($batteryFields[3]),$adbState" |
        Add-Content -Path $samplePath -Encoding utf8

    if (([DateTime]::UtcNow - $endUtc).TotalSeconds -lt 0 -and (($nowLocal.Minute % 5) -eq 0) -and ($nowLocal.Second -lt 32)) {
        $detailNo++
        $power = & adb -s $serial shell dumpsys power 2>$null
        $wakeLine = $power | Select-String -Pattern 'mWakefulness=' | Select-Object -First 1
        $wake = if ($wakeLine) { ($wakeLine.Line -replace ',', ';').Trim() } else { '' }
        $idle = & adb -s $serial shell dumpsys deviceidle 2>$null
        $stateLine = ($idle | Select-String -Pattern '^\s*mState=' | Select-Object -First 1).Line
        $screenLine = ($idle | Select-String -Pattern '^\s*mScreenOn=' | Select-Object -First 1).Line
        $lockedLine = ($idle | Select-String -Pattern '^\s*mScreenLocked=' | Select-Object -First 1).Line
        $bucketJd = (& adb -s $serial shell am get-standby-bucket com.jingdong.app.mall 2>$null | Out-String).Trim()
        $notifyJd = ((& adb -s $serial shell cmd appops get com.jingdong.app.mall POST_NOTIFICATION 2>$null | Out-String).Trim() -replace '[\r\n]+', ';') -replace ',', ';'
        $bucketAi = (& adb -s $serial shell am get-standby-bucket com.xiaomi.aicr 2>$null | Out-String).Trim()
        $audioAi = ((& adb -s $serial shell cmd appops get com.xiaomi.aicr RECORD_AUDIO 2>$null | Out-String).Trim() -replace '[\r\n]+', ';') -replace ',', ';'
        "$stamp,$wake,$stateLine,$screenLine,$lockedLine,$bucketJd,$notifyJd,$bucketAi,$audioAi" |
            Add-Content -Path $statePath -Encoding utf8

        $psLines = @(adb -s $serial shell ps -A -o PID,NAME 2>$null)
        foreach ($line in $psLines) {
            $entry = $line.ToString().Trim()
            if ($entry -match '^(\d+)\s+(\S+)$') {
                $processId = $Matches[1]
                $name = $Matches[2]
                if (($name -like 'com.jingdong.app.mall*') -or ($name -like 'com.xiaomi.aicr*') -or ($name -like 'cn.myflv.noactive*') -or ($name -like 'com.dsh.noactivemonitor*')) {
                    $stat = (& adb -s $serial shell su -c "cat /proc/$processId/stat" 2>$null | Out-String).Trim()
                    if ($stat -match '^\d+\s+\((.*)\)\s+(\S+)\s+(.*)$') {
                        $comm = $Matches[1]
                        $procState = $Matches[2]
                        $tail = @($Matches[3] -split '\s+')
                        if ($tail.Count -ge 19) {
                            "$stamp,$name,$processId,$procState,$($tail[18]),$($tail[10]),$($tail[11])" |
                                Add-Content -Path $procPath -Encoding utf8
                        }
                    }
                }
            }
        }

        if (($detailNo % 3) -eq 1) {
            $snapName = $nowLocal.ToString('yyyyMMdd-HHmm') + '-charged.txt'
            & adb -s $serial shell dumpsys batterystats --charged 2>&1 |
                Set-Content -Path (Join-Path $snapshotDir $snapName) -Encoding utf8
        }
        Add-Content -Path $logPath -Value "$stamp detail=$detailNo adb=$adbState doze=$stateLine" -Encoding utf8
    }

    Start-Sleep -Seconds 30
}

$finalLocal = [TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $tz)
$finalName = $finalLocal.ToString('yyyyMMdd-HHmm') + '-charged-final.txt'
& adb -s $serial shell dumpsys batterystats --charged 2>&1 |
    Set-Content -Path (Join-Path $snapshotDir $finalName) -Encoding utf8
Add-Content -Path $logPath -Value "$($finalLocal.ToString('yyyy-MM-dd HH:mm:ss')) completed" -Encoding utf8
