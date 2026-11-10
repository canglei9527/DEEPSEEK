$ErrorActionPreference = 'Continue'
$serial = '192.168.1.89:45051'
$outDir = Join-Path (Get-Location) 'work\battery-debug\live-session-02'
$logPath = Join-Path $outDir 'post-restriction-v2.csv'
$beforePath = Join-Path $outDir 'batterystats-before-v2.checkin'
$afterPath = Join-Path $outDir 'batterystats-after-v2.checkin'

'host_time,charge_counter_uAh,current_now_uA,capacity_pct,battery_status,wakefulness,jd_standby_bucket,jd_notification_appop,jd_pids,jd_cpuinfo' |
    Set-Content -Path $logPath -Encoding utf8

& adb -s $serial shell dumpsys batterystats --checkin 2>&1 |
    Set-Content -Path $beforePath -Encoding utf8

for ($i = 0; $i -lt 90; $i++) {
    $hostTime = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    $batteryRead = & adb -s $serial shell su -c 'cat /sys/class/power_supply/battery/charge_counter /sys/class/power_supply/battery/current_now /sys/class/power_supply/battery/capacity /sys/class/power_supply/battery/status' 2>$null
    $batteryFields = @($batteryRead | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    while ($batteryFields.Count -lt 4) { $batteryFields += '' }
    $wake = ''
    $bucket = ''
    $notify = ''
    $pids = ''
    $jdCpu = ''
    if (($i % 3) -eq 0) {
        $power = & adb -s $serial shell dumpsys power 2>$null
        $wakeLine = $power | Select-String -Pattern 'mWakefulness=' | Select-Object -First 1
        if ($wakeLine) { $wake = ($wakeLine.Line -replace ',', ';').Trim() }
        $bucket = (& adb -s $serial shell am get-standby-bucket com.jingdong.app.mall 2>$null | Out-String).Trim()
        $notify = (& adb -s $serial shell cmd appops get com.jingdong.app.mall POST_NOTIFICATION 2>$null | Out-String).Trim() -replace ',', ';'
        $pids = (& adb -s $serial shell pidof com.jingdong.app.mall 2>$null | Out-String).Trim()
        $cpu = & adb -s $serial shell su -c 'dumpsys cpuinfo | grep -m1 com.jingdong.app.mall' 2>$null
        $jdCpu = (($cpu -join ' ') -replace ',', ';').Trim()
    }
    $row = @($hostTime, $batteryFields[0], $batteryFields[1], $batteryFields[2], $batteryFields[3], $wake, $bucket, $notify, $pids, $jdCpu) -join ','
    $row | Add-Content -Path $logPath -Encoding utf8
    Start-Sleep -Seconds 10
}

& adb -s $serial shell dumpsys batterystats --checkin 2>&1 |
    Set-Content -Path $afterPath -Encoding utf8

Get-Content -Path $logPath -Tail 8
