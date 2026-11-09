#!/system/bin/sh
# fast.sh -- 轻量快照（不逐进程 fork，避免在 load 18 的机器上把 adbd 拖死）
# 用法: su -c "sh /data/local/tmp/fast.sh"

echo "== load =="; cat /proc/loadavg
echo "== mem =="; grep -E 'MemTotal|MemFree|MemAvailable|Cached:|SwapTotal|SwapFree|AnonPages' /proc/meminfo
echo "== swaps =="; cat /proc/swaps
echo "== zram =="; cat /sys/block/zram0/mm_stat 2>/dev/null
echo "== psi cpu =="; cat /proc/pressure/cpu 2>/dev/null
echo "== psi mem =="; cat /proc/pressure/memory 2>/dev/null
echo "== vmstat =="; grep -E 'pgscan_direct|pgscan_kswapd|pgsteal_direct|pgsteal_kswapd|compact_stall|allocstall' /proc/vmstat 2>/dev/null
echo "== procs =="; ps -A -o PID,RSS,NAME 2>/dev/null | awk 'NR>1 && $2>150000 {printf "  rss=%6.1fMB %s\n", $2/1024, $3}' | sort -k2 -nr | head -n 10
echo "== suspend =="; for f in success fail last_failed_errno last_failed_dev; do printf '  %-18s %s\n' "$f" "$(cat /sys/power/suspend_stats/$f 2>/dev/null)"; done
echo "== dmesg tail =="; dmesg 2>/dev/null | grep -E 'PM: suspend|Abort: Pending' | tail -n 6
echo "== net =="
for n in wlan0 rmnet_data1; do
  echo "  $n rx=$(cat /sys/class/net/$n/statistics/rx_packets 2>/dev/null) tx=$(cat /sys/class/net/$n/statistics/tx_packets 2>/dev/null)"
done
echo "== battery =="; for f in status capacity charge_counter current_now voltage_now temp; do printf '  %-14s %s\n' "$f" "$(cat /sys/class/power_supply/battery/$f 2>/dev/null)"; done