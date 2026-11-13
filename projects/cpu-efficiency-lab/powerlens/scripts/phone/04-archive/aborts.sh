#!/system/bin/sh
# aborts.sh -- 统计 dmesg 里挂起中止的来源分布，并统计 D 态任务
# 用法: su -c "sh /data/local/tmp/aborts.sh"

echo "== 最近 suspend entry / exit / abort 总量 =="
E=$(dmesg 2>/dev/null | grep -c 'PM: suspend entry')
X=$(dmesg 2>/dev/null | grep -c 'PM: suspend exit')
A=$(dmesg 2>/dev/null | grep -c 'Abort: Pending Wakeup Sources')
echo "  entry=$E exit=$X abort=$A"
if [ "$E" -gt 0 ]; then
  echo "  中止率: $((A * 100 / E))%"
fi

echo
echo "== 中止来源频次 top 20 =="
dmesg 2>/dev/null | grep 'Abort: Pending Wakeup Sources' \
  | sed 's/.*Pending Wakeup Sources: //' \
  | tr ' ' '\n' | grep -v '^$' | sort | uniq -c | sort -nr | head -n 20

echo
echo "== 成功挂起时长分布（suspend entry -> exit 的时间差，单位 ms） =="
dmesg 2>/dev/null | grep -E 'PM: suspend (entry|exit)' | tail -n 200 | awk '
/PM: suspend entry/ { match($0, /\[ *([0-9]+\.[0-9]+)\]/, m1); t0=m1[1]; kind="entry" ; next }
/PM: suspend exit/  { match($0, /\[ *([0-9]+\.[0-9]+)\]/, m2); t1=m2[1];
                      if (t0 != "") { printf "%.0f\n", (t1-t0)*1000; t0="" } }
' | sort -n | awk '{a[NR]=$1} END{
  if (NR==0) { print "  (无数据)"; exit }
  printf "  n=%d  min=%sms  p50=%sms  p90=%sms  max=%sms\n", NR, a[1], a[int(NR*0.5)+1], a[int(NR*0.9)+1], a[NR]
}'

echo
echo "== D 态任务快照（连续 5 次，每次 0.3s） =="
for i in 1 2 3 4 5; do
  n=0
  for d in /proc/[0-9]*; do
    st=$(awk '{print $3}' "$d/stat" 2>/dev/null)
    [ "$st" = "D" ] && n=$((n+1))
  done
  echo "  sample$i D=$n load=$(cut -d' ' -f1 /proc/loadavg)"
  sleep 0.3 2>/dev/null || sleep 1
done

echo
echo "== 内核线程总数 vs 用户进程 =="
K=0; U=0
for d in /proc/[0-9]*; do
  nm=$(grep '^Name:' "$d/status" 2>/dev/null | cut -f2)
  case "$nm" in
    \[*) K=$((K+1)) ;;
    *)   U=$((U+1)) ;;
  esac
done
echo "  kernel_threads=$K  user_procs=$U"

echo
echo "== kworker 线程数 =="
ps -A -o NAME 2>/dev/null | grep -c '^\[kworker'
echo "== kworker 按后缀分类 top 12 =="
ps -A -o NAME 2>/dev/null | grep '^\[kworker' | sed 's/[0-9:\/]*//g' | sort | uniq -c | sort -nr | head -n 12