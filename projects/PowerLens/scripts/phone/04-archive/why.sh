#!/system/bin/sh
# why.sh -- 一次性回答「为什么待机耗电」：内存压力 / zram / D 态 / 唤醒源 / 移动数据
# 用法: su -c "sh /data/local/tmp/why.sh"

echo "########## 1. 负载与 D 态 ##########"
cat /proc/loadavg
echo -n 'D 态任务数: '
for d in /proc/[0-9]*; do
  st=$(awk '{print $3}' "$d/stat" 2>/dev/null)
  [ "$st" = "D" ] && echo -n '.'
done
echo
echo '--- D 态明细 ---'
for d in /proc/[0-9]*; do
  pid=${d#/proc/}
  st=$(awk '{print $3}' "$d/stat" 2>/dev/null)
  [ "$st" = "D" ] || continue
  c=$(tr -d '\000' < "$d/cmdline" 2>/dev/null | cut -c1-55)
  [ -z "$c" ] && c=$(grep '^Name:' "$d/status" | cut -f2)
  wc_=$(cat "$d/wchan" 2>/dev/null)
  echo "  pid=$pid wchan=$wc_ $c"
done | head -n 20

echo
echo "########## 2. 内存 / 交换 ##########"
grep -E 'MemTotal|MemFree|MemAvailable|Cached|SwapTotal|SwapFree|AnonPages|Slab|SReclaimable' /proc/meminfo
echo '--- swaps ---'; cat /proc/swaps
echo '--- zram mm_stat ---'; cat /sys/block/zram0/mm_stat 2>/dev/null
echo '--- PSI ---'
cat /proc/pressure/cpu 2>/dev/null
cat /proc/pressure/memory 2>/dev/null
cat /proc/pressure/io 2>/dev/null

echo
echo "########## 3. vmstat 计数（累计） ##########"
grep -E 'pgscan|pgsteal|pgmajfault|compact_stall|compact_fail|allocstall|kswapd' /proc/vmstat 2>/dev/null | head -n 20

echo
echo "########## 4. 按 VmSwap 排序 top 12 ##########"
for d in /proc/[0-9]*; do
  pid=${d#/proc/}
  s=$(grep '^VmSwap:' "$d/status" 2>/dev/null | awk '{print $2}')
  [ -z "$s" ] && continue
  [ "$s" -ge 32768 ] || continue
  c=$(tr -d '\000' < "$d/cmdline" 2>/dev/null | cut -c1-50)
  [ -z "$c" ] && c=$(grep '^Name:' "$d/status" | cut -f2)
  echo "$pid $s $c"
done | sort -k2 -nr | head -n 12 | awk '{printf "  swap=%7.1f MB  pid=%-7s %s\n", $2/1024, $1, substr($0, index($0,$3))}'

echo
echo "########## 5. 按 VmRSS 排序 top 12 ##########"
for d in /proc/[0-9]*; do
  pid=${d#/proc/}
  s=$(grep '^VmRSS:' "$d/status" 2>/dev/null | awk '{print $2}')
  [ -z "$s" ] && continue
  [ "$s" -ge 102400 ] || continue
  c=$(tr -d '\000' < "$d/cmdline" 2>/dev/null | cut -c1-50)
  [ -z "$c" ] && c=$(grep '^Name:' "$d/status" | cut -f2)
  echo "$pid $s $c"
done | sort -k2 -nr | head -n 12 | awk '{printf "  rss=%7.1f MB  pid=%-7s %s\n", $2/1024, $1, substr($0, index($0,$3))}'

echo
echo "########## 6. 进程/线程总数 ##########"
echo -n 'processes: '; ls -d /proc/[0-9]* 2>/dev/null | wc -l
echo -n 'threads:   '; ls -d /proc/[0-9]*/task/* 2>/dev/null | wc -l

echo
echo "########## 7. 挂起失败详情 ##########"
for f in success fail last_failed_errno last_failed_dev; do
  printf '  %-18s %s\n' "$f" "$(cat /sys/power/suspend_stats/$f 2>/dev/null)"
done
echo '--- 最近 8 次 suspend 结果 ---'
dmesg 2>/dev/null | grep -E 'PM: suspend|Abort: Pending' | tail -n 8

echo
echo "########## 8. 移动数据 / 调制解调器 ##########"
settings get global mobile_data | sed 's/^/  mobile_data=/' 
settings get global airplane_mode_on | sed 's/^/  airplane_mode=/'
echo -n '  rmnet 接口: '; ip -o link show 2>/dev/null | grep -c rmnet
echo '--- rmnet 计数 ---'
for i in /sys/class/net/rmnet*/statistics/rx_packets; do
  n=$(echo "$i" | cut -d/ -f5)
  printf '  %-14s rx=%-10s tx=%s\n' "$n" "$(cat $i 2>/dev/null)" "$(cat /sys/class/net/$n/statistics/tx_packets 2>/dev/null)"
done

echo
echo "########## 9. Wi-Fi 省电与链路 ##########"
dumpsys wifi 2>/dev/null | grep -iE 'mWifiInfo|rssi|txpower|link speed|power save|Wi-Fi is' | head -n 8
echo -n '  wlan0 rx_packets: '; cat /sys/class/net/wlan0/statistics/rx_packets 2>/dev/null
echo -n '  wlan0 tx_packets: '; cat /sys/class/net/wlan0/statistics/tx_packets 2>/dev/null
echo -n '  wlan0 rx_bytes:   '; cat /sys/class/net/wlan0/statistics/rx_bytes 2>/dev/null

echo
echo "########## 10. 电池 ##########"
for f in status capacity charge_counter current_now voltage_now temp; do
  printf '  %-14s %s\n' "$f" "$(cat /sys/class/power_supply/battery/$f 2>/dev/null)"
done