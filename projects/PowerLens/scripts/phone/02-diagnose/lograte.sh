#!/system/bin/sh
# lograte.sh -- 实测日志产生速率 + 找出实时高频 tag
# 用法: su -c "sh /data/local/tmp/lograte.sh [秒数，默认10]"

WIN=${1:-10}

echo "===== 1. 实时日志速率（${WIN}s）====="
# 用 logcat -c 清空后统计会漏历史，改用两次快照的时间戳差
T0=$(date +%s%N 2>/dev/null || date +%s)
N0=$(logcat -b main -d -v threadtime 2>/dev/null | wc -l)
sleep $WIN
N1=$(logcat -b main -d -v threadtime 2>/dev/null | wc -l)
T1=$(date +%s%N 2>/dev/null || date +%s)
echo "  main buffer: $N0 -> $N1 行"
echo "  （buffer 有上限，此值仅供参照）"

echo ""
echo "===== 2. 清空后 10 秒的纯增量（最准）====="
logcat -b main -c 2>/dev/null
sleep 10
TOTAL=$(logcat -b main -d 2>/dev/null | wc -l)
echo "  10 秒产生 $TOTAL 行 = $((TOTAL/10)) 行/秒"

echo ""
echo "===== 3. 这 10 秒内的 tag 分布（top 12）====="
logcat -b main -d -v threadtime 2>/dev/null | awk 'NF>=6 {print $6}' | sed 's/:$//' | sort | uniq -c | sort -nr | head -n 12 | sed 's/^/  /'

echo ""
echo "===== 4. 这 10 秒内的 PID 分布（top 10）====="
logcat -b main -d -v threadtime 2>/dev/null | awk 'NF>=6 {
  pid=$3
  cnt[pid]++
} END {
  for (p in cnt) print cnt[p], p
}' | sort -nr | head -n 10 | while read n pid; do
  nm=$(tr -d '\000' < /proc/$pid/cmdline 2>/dev/null | head -c 40)
  [ -z "$nm" ] && nm=$(grep '^Name:' /proc/$pid/status 2>/dev/null | cut -f2)
  printf '  %-6s 条  pid=%-7s %s\n' "$n" "$pid" "${nm:-?}"
done

echo ""
echo "===== 5. 最频繁的一行（原样）====="
logcat -b main -d -v threadtime 2>/dev/null | sed 's/^[0-9-]* [0-9:.]* *[0-9]* *[0-9]* //' | sort | uniq -c | sort -nr | head -n 3 | sed 's/^/  /'

echo ""
echo "===== 6. system buffer 同样统计 ====="
logcat -b system -c 2>/dev/null
sleep 10
T2=$(logcat -b system -d 2>/dev/null | wc -l)
echo "  10 秒产生 $T2 行 = $((T2/10)) 行/秒"
logcat -b system -d -v threadtime 2>/dev/null | awk 'NF>=6 {print $6}' | sed 's/:$//' | sort | uniq -c | sort -nr | head -n 8 | sed 's/^/  /'