$ErrorActionPreference = 'Continue'
$serial = '192.168.1.89:45051'
$outDir = Join-Path (Get-Location) 'work\battery-debug\live-session-02'
$logPath = Join-Path $outDir 'post-restriction.csv'
$beforePath = Join-Path $outDir 'batterystats-before.checkin'
$afterPath = Join-Path $outDir 'batterystats-after.checkin'

'host_time,device_epoch,charge_counter,current_now,current_avg,capacity,status,wakefulness,jd_standby_bucket,jd_notification_appop,jd_pids,jd_cpuinfo' |
    Set-Content -Path $logPath -Encoding utf8

& adb -s $serial shell dumpsys batterystats --checkin 2>&1 |
    Set-Content -Path $beforePath -Encoding utf8

for ($i = 0; $i -lt 30; $i++) {
    $hostTime = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    $sample = & adb -s $serial shell su -c 'echo "$(date +%s),$(cat /sys/class/power_supply/battery/charge_counter 2>/dev/null),$(cat /sys/class/power_supply/battery/current_now 2>/dev/null),$(cat /sys/class/power_supply/battery/current_avg 2>/dev/null),$(cat /sys/class/power_supply/battery/capacity 2>/dev/null),$(cat /sys/class/power_supply/battery/status 2>/dev/null),$(dumpsys power | grep -m1 mWakefulness | tr -d " "),$(am get-standby-bucket com.jingdong.app.mall),$(cmd appops get com.jingdong.app.mall POST_NOTIFICATION | tr -d "\r\n"),$(pidof com.jingdong.app.mall)"' 2>$null
    $sampleText = ($sample -join '').Trim()
    $jdCpu = 'not sampled'
    if (($i % 2) -eq 0) {
        $cpu = & adb -s $serial shell su -c 'dumpsys cpuinfo | grep -m1 com.jingdong.app.mall' 2>$null
        $jdCpu = (($cpu -join ' ') -replace ',', ';').Trim()
        if (-not $jdCpu) { $jdCpu = 'no cpuinfo line' }
    }
    if (-not $sampleText) { $sampleText = 'adb_sample_error' }
    "$hostTime,$sampleText,$jdCpu" | Add-Content -Path $logPath -Encoding utf8
    Start-Sleep -Seconds 30
}

& adb -s $serial shell dumpsys batterystats --checkin 2>&1 |
    Set-Content -Path $afterPath -Encoding utf8

Get-Content -Path $logPath -Tail 5
