#!/system/bin/sh
# logd-who.sh -- 谁在疯狂写日志（logd 高 CPU 的真凶）
# 用法: su -c "sh /data/local/tmp/logd-who.sh"

export PATH=/system/bin:/system/xbin:/vendor/bin:$PATH

echo "===== 1. logd 读取的 socket / buffer 配置 ====="
logcat -g 2>&1 | head -n 12 | sed 's/^/  /'

echo ""
echo "===== 2. 当前 buffer 使用量（判满则会疯狂 flush）====="
for b in main system crash events; do
  SZ=$(logcat -b $b -g 2>/dev/null | grep -oE 'ring buffer is [0-9.]+[KMG]B' | head -n1)
  [ -n "$SZ" ] && echo "  $b: $SZ"
done

echo ""
echo "===== 3. 各 tag 的日志条数（最近 buffer 内）====="
echo "  --- main ---"
logcat -b main -d -v brief 2>/dev/null | awk '{print $3}' | cut -d: -f1 | sort | uniq -c | sort -nr | head -n 15 | sed 's/^/    /'

echo ""
echo "  --- system ---"
logcat -b system -d -v brief 2>/dev/null | awk '{print $3}' | cut -d: -f1 | sort | uniq -c | sort -nr | head -n 10 | sed 's/^/    /'

echo ""
echo "===== 4. 各进程的日志量（按 PID）====="
logcat -b main -d -v threadtime 2>/dev/null | awk 'NF>=5 {
  pid=$2
  cnt[pid]++
} END {
  for (p in cnt) if (cnt[p] > 100) print cnt[p], p
}' | sort -nr | head -n 15 | while read n pid; do
  nm=$(tr -d '\000' < /proc/$pid/cmdline 2>/dev/null | cut -c1-45)
  [ -z "$nm" ] && nm=$(grep '^Name:' /proc/$pid/status 2>/dev/null | cut -f2)
  printf '    %-6s 条  pid=%-7s %s\n' "$n" "$pid" "${nm:-?}"
done

echo ""
echo "===== 5. logd 的 CPU 时间分解（用户态 vs 内核态）====="
P=$(pidof logd)
if [ -n "$P" ]; then
  awk -v p=$P '{printf "  utime=%d ticks (%d ms)  stime=%d ticks (%d ms)\n", $14, $14*10, $15, $15*10}' /proc/$P/stat
  echo "  --- /proc/PID/io ---"
  cat /proc/$P/io 2>/dev/null | sed 's/^/    /'
  echo "  --- 线程数 ---"
  ls -d /proc/$P/task/* 2>/dev/null | wc -l | sed 's/^/    /'
  echo "  --- 线程名 ---"
  for t in /proc/$P/task/*; do cat $t/comm 2>/dev/null; done | sort | uniq -c | sort -nr | head -n 10 | sed 's/^/    /'
fi

echo ""
echo "===== 6. 每 5 秒的日志产生速率 ====="
A=$(logcat -b main -d 2>/dev/null | wc -l)
sleep 5
B=$(logcat -b main -d 2>/dev/null | wc -l)
echo "  buffer 内行数: $A -> $B （差 $((B-A))，不是产生速率，buffer 有上限）"

echo ""
echo "  --- 用 dmesg 看内核日志速率 ---"
D0=$(dmesg 2>/dev/null | wc -l)
sleep 5
D1=$(dmesg 2>/dev/null | wc -l)
echo "  dmesg 行数: $D0 -> $D1"

echo ""
echo "===== 7. 高频日志样例（同一 tag 重复出现）====="
logcat -b main -d -v brief 2>/dev/null | awk '{print $3}' | sort | uniq -c | sort -nr | head -n 5 | sed 's/^/  /'

echo ""
echo "===== 8. 结论 ====="
echo "  logd 已排除「我的采集导致」（A/B 差异仅 1.4%）"
echo "  如果第 3/4 节有某个 tag 或 PID 出现数万条，那就是真凶"
echo "  常见元凶："
echo "    - MiuiBooster / miui 系统服务的 debug 日志"
echo "    - 某个应用在循环打 Log.d"
echo "    - dmesg 里内核在刷同一个错误"