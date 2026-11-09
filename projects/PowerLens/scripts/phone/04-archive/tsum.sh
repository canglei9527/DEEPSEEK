#!/system/bin/sh
# tsum.sh -- 单 awk 扫所有 /proc/*/status，不逐进程 fork（load 18 也不卡）
# 用法: su -c "sh /data/local/tmp/tsum.sh"

echo "== 线程数 top 20 =="
awk 'FNR==1{nm="";th=0} /^Name:/{nm=$2} /^Threads:/{th=$2; split(FILENAME,a,"/"); print th"\t"a[3]"\t"nm}' \
    /proc/[0-9]*/status 2>/dev/null \
  | sort -nr | head -n 20 \
  | awk -F'\t' '{printf "  %-6s thread  pid=%-7s %s\n", $1, $2, $3}'

echo
echo "== 线程总数 / 进程总数 =="
awk '/^Threads:/{s+=$2; n++} END{printf "  processes=%d  threads=%d\n", n, s}' /proc/[0-9]*/status 2>/dev/null

echo
echo "== RSS top 12（MB） =="
awk '/^Name:/{nm=$2} /^VmRSS:/{printf "%d\t%s\t%s\n", $2/1024, FILENAME, nm}' /proc/[0-9]*/status 2>/dev/null \
  | sort -nr | head -n 12 | awk -F'\t' '{split($2,a,"/"); printf "  %7s MB  pid=%-7s %s\n", $1, a[3], $3}'

echo
echo "== Swap top 12（MB） =="
awk '/^Name:/{nm=$2} /^VmSwap:/{if($2>0) printf "%d\t%s\t%s\n", $2/1024, FILENAME, nm}' /proc/[0-9]*/status 2>/dev/null \
  | sort -nr | head -n 12 | awk -F'\t' '{split($2,a,"/"); printf "  %7s MB  pid=%-7s %s\n", $1, a[3], $3}'

echo
echo "== mem / zram / psi =="
grep -E 'MemTotal|MemFree|MemAvailable|Cached:|SwapTotal|SwapFree|AnonPages' /proc/meminfo
cat /proc/swaps
echo -n '  mm_stat: '; cat /sys/block/zram0/mm_stat 2>/dev/null
cat /proc/pressure/memory 2>/dev/null

echo
echo "== suspend =="
for f in success fail last_failed_errno last_failed_dev; do
  printf '  %-18s %s\n' "$f" "$(cat /sys/power/suspend_stats/$f 2>/dev/null)"
done
dmesg 2>/dev/null | grep -E 'PM: suspend|Abort: Pending' | tail -n 4

echo
echo "== battery =="
for f in status capacity charge_counter current_now voltage_now temp; do
  printf '  %-14s %s\n' "$f" "$(cat /sys/class/power_supply/battery/$f 2>/dev/null)"
done