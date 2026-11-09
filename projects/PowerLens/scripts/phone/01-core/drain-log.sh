#!/system/bin/sh
# drain-log.sh -- 息屏待机取样器（自己跑，不依赖 adb 在线）
# 用法: su -c "setsid sh /data/local/tmp/drain-log.sh 900 >/dev/null 2>&1 &"
#   $1 = 总时长秒数（默认 900）
# 输出: /data/local/tmp/drain-<启动时间>.csv
#
# 每 15 s 一行，记录：电量计、瞬时电流、挂起成功/失败累计、dmesg 里 suspend entry 次数、
# Wi-Fi 收发包、loadavg、MemFree、zram 已用。这样息屏后 adb 掉线也不丢数据。

DUR=${1:-900}
STEP=15
OUT=/data/local/tmp/drain-$(date +%Y%m%d-%H%M%S).csv

hc() { cat /sys/class/power_supply/battery/charge_counter 2>/dev/null; }
cur() { cat /sys/class/power_supply/battery/current_now 2>/dev/null; }
vol() { cat /sys/class/power_supply/battery/voltage_now 2>/dev/null; }
cap() { cat /sys/class/power_supply/battery/capacity 2>/dev/null; }
tmp() { cat /sys/class/power_supply/battery/temp 2>/dev/null; }
ss_ok() { cat /sys/power/suspend_stats/success 2>/dev/null; }
ss_fail() { cat /sys/power/suspend_stats/fail 2>/dev/null; }
lfd() { cat /sys/power/suspend_stats/last_failed_dev 2>/dev/null; }
ent() { dmesg 2>/dev/null | grep -c 'PM: suspend entry'; }
abr() { dmesg 2>/dev/null | grep -c 'Abort: Pending Wakeup Sources'; }
wr() { cat /sys/class/net/wlan0/statistics/rx_packets 2>/dev/null; }
wt() { cat /sys/class/net/wlan0/statistics/tx_packets 2>/dev/null; }
ld() { cut -d' ' -f1 /proc/loadavg 2>/dev/null; }
mf() { grep '^MemFree:' /proc/meminfo 2>/dev/null | awk '{print $2}'; }
sw() { grep '^SwapFree:' /proc/meminfo 2>/dev/null | awk '{print $2}'; }
wk() { tr '\n' '|' < /sys/power/wake_lock 2>/dev/null; }

echo "epoch,iso,charge_counter_uAh,current_now_uA,voltage_uV,capacity,temp_dC,suspend_ok,suspend_fail,last_failed_dev,suspend_entry_cnt,abort_cnt,wlan_rx_pkt,wlan_tx_pkt,load1,memfree_kB,swapfree_kB,wakelocks" > "$OUT"

END=$(( $(date +%s) + DUR ))
while [ "$(date +%s)" -lt "$END" ]; do
    printf '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n' \
        "$(date +%s)" "$(date '+%F %T')" "$(hc)" "$(cur)" "$(vol)" "$(cap)" "$(tmp)" \
        "$(ss_ok)" "$(ss_fail)" "$(lfd)" "$(ent)" "$(abr)" "$(wr)" "$(wt)" \
        "$(ld)" "$(mf)" "$(sw)" "$(wk)" >> "$OUT"
    sleep $STEP
done

echo "done $OUT" >> /data/local/tmp/drain-log.path