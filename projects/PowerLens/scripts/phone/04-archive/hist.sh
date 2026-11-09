#!/system/bin/sh
# hist.sh -- 挂起循环统计（只用 POSIX awk，老 busybox mawk 也能跑）
# 用法: su -c "sh /data/local/tmp/hist.sh"

echo "== 总量 =="
E=$(dmesg 2>/dev/null | grep -c 'PM: suspend entry')
X=$(dmesg 2>/dev/null | grep -c 'PM: suspend exit')
A=$(dmesg 2>/dev/null | grep -c 'Abort: Pending Wakeup Sources')
S=$(dmesg 2>/dev/null | grep -c 'PM: suspend entry (deep)')
echo "  entry=$E exit=$X abort=$A deep=$S"
[ "$E" -gt 0 ] && echo "  中止率=$((A * 100 / E))%"

echo
echo "== 中止来源频次 top 20 =="
dmesg 2>/dev/null | grep 'Abort: Pending Wakeup Sources' \
  | sed 's/.*Pending Wakeup Sources: //' | tr ' ' '\n' | grep -v '^$' \
  | sort | uniq -c | sort -nr | head -n 20

echo
echo "== suspend entry -> exit 时长分布（ms） =="
dmesg 2>/dev/null | grep -E 'PM: suspend (entry|exit)' | tail -n 300 > /data/local/tmp/_s.txt
awk '
{
  line = $0
  # 取出 [  12345.678901] 里的时间戳
  sub(/^\[ */, "", line)
  sub(/\].*$/, "", line)
  t = line + 0
  if ($0 ~ /suspend entry/) { t0 = t; have = 1 }
  else if ($0 ~ /suspend exit/ && have) { printf "%.0f\n", (t - t0) * 1000; have = 0 }
}' /data/local/tmp/_s.txt | sort -n | awk '
{ a[NR] = $1; s += $1 }
END {
  if (NR == 0) { print "  (无数据)"; exit }
  printf "  n=%d  min=%dms  p50=%dms  p90=%dms  max=%dms  mean=%dms\n",
         NR, a[1], a[int(NR*0.5)+1], a[int(NR*0.9)+1], a[NR], s/NR
}'
rm -f /data/local/tmp/_s.txt

echo
echo "== dmesg 里 suspend 区块时间跨度 =="
dmesg 2>/dev/null | grep -E 'PM: suspend entry|Abort: Pending|PM: suspend exit' | tail -n 6

echo
echo "== D 态连续采样 =="
i=1
while [ $i -le 5 ]; do
  n=0
  for d in /proc/[0-9]*; do
    st=$(awk '{print $3}' "$d/stat" 2>/dev/null)
    [ "$st" = "D" ] && n=$((n+1))
  done
  echo "  sample$i D=$n load=$(cut -d' ' -f1 /proc/loadavg)"
  i=$((i+1))
  sleep 1
done