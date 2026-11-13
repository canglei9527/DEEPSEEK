#!/system/bin/sh
# probe.sh -- 一次性把电源/挂起现状打印干净，避免 PC 端引号地狱
# 用法: su -c "sh /data/local/tmp/probe.sh"

echo "===== TIME/UP ====="
date '+%F %T'; cat /proc/uptime

echo
echo "===== BATTERY ====="
for f in status capacity charge_counter current_now voltage_now temp cycle_count charge_full charge_full_design; do
    v=$(cat /sys/class/power_supply/battery/$f 2>/dev/null)
    printf '%-20s %s\n' "$f" "$v"
done

echo
echo "===== SUSPEND CAP ====="
echo -n 'state:      '; cat /sys/power/state 2>/dev/null
echo -n 'mem_sleep:  '; cat /sys/power/mem_sleep 2>/dev/null

echo
echo "===== SUSPEND STATS ====="
for f in success fail failed total_hw_sleep total_resume_count last_failed_errno last_failed_dev; do
    v=$(cat /sys/power/suspend_stats/$f 2>/dev/null)
    printf '%-22s %s\n' "$f" "$v"
done

echo
echo "===== WAKEUP SOURCES (top 20 by count) ====="
if [ -r /sys/kernel/debug/wakeup_sources ]; then
    head -n 1 /sys/kernel/debug/wakeup_sources
    tail -n +2 /sys/kernel/debug/wakeup_sources | sort -k7 -nr | head -n 20
elif [ -r /sys/kernel/debug/wakeup_sources/wakeup_sources ]; then
    echo "(nested path)"
    head -n 1 /sys/kernel/debug/wakeup_sources/wakeup_sources
    tail -n +2 /sys/kernel/debug/wakeup_sources/wakeup_sources | sort -k7 -nr | head -n 20
else
    echo "(debug wakeup_sources 不可读，试 dumpsys power)"
fi

echo
echo "===== ACTIVE WAKE LOCKS ====="
cat /sys/power/wake_lock 2>/dev/null | sed 's/^/  /'
echo "(以上为 /sys/power/wake_lock 内容，空则无用户态锁)"

echo
echo "===== SCREEN / POWER ====="
dumpsys power 2>/dev/null | grep -E 'mWakefulness=|mIsPowered=|mBatteryLevel=|mScreenOn' | head -n 10

echo
echo "===== AOD/DOZE ====="
settings get secure doze_always_on 2>/dev/null | sed 's/^/doze_always_on: /'
settings get system screen_off_timeout 2>/dev/null | sed 's/^/screen_off_timeout: /'

echo
echo "===== PM SUSPEND 最近 12 条 ====="
dmesg 2>/dev/null | grep -E 'PM: suspend|Abort: Pending' | tail -n 12