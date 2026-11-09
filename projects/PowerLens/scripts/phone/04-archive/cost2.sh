#!/system/bin/sh
# cost2.sh -- 开销量化（POSIX 版，避开 /proc/stat 多字段解析问题）
# 用法: su -c "sh /data/local/tmp/cost2.sh"

echo "########## A. 各数据源单次读取成本 ##########"
bench_ms() {
  name=$1
  cmd=$2
  n=$3
  t0=$(date +%s)
  i=0
  while [ $i -lt $n ]; do
    sh -c "$cmd" > /dev/null 2>&1
    i=$((i+1))
  done
  t1=$(date +%s)
  ms=$(( (t1 - t0) * 1000 / n ))
  printf '  %-52s %6s ms/次\n' "$name" "$ms"
}

bench_ms "cat /proc/uid_cputime/show_uid_stat" "cat /proc/uid_cputime/show_uid_stat" 20
bench_ms "cat /proc/uid_io/stats" "cat /proc/uid_io/stats" 20
bench_ms "cat charge_counter" "cat /sys/class/power_supply/battery/charge_counter" 20
bench_ms "cat /proc/meminfo" "cat /proc/meminfo" 20
bench_ms "dumpsys batterystats --charged" "dumpsys batterystats --charged" 3
bench_ms "dumpsys batterystats (无参)" "dumpsys batterystats" 2

echo
echo "########## B. 输出体积对比 ##########"
for v in "--charged" "" "--proto" "--checkin"; do
  SZ=$(su -c "dumpsys batterystats $v" 2>/dev/null | wc -c)
  printf '  dumpsys batterystats %-10s %10s 字节\n' "$v" "$SZ"
done

echo
echo "########## C. 系统级 per-UID 数据源清单 ##########"
for f in /proc/uid_cputime/show_uid_stat /proc/uid_io/stats /proc/uid_procstat/set \
         /sys/fs/bpf/map_uid_system_cpu_time /sys/fs/bpf/map_uid_time_in_state \
         /sys/kernel/tracing/trace_marker /proc/wakeup_sources; do
  if [ -e "$f" ]; then
    SZ=$(wc -c < "$f" 2>/dev/null)
    LN=$(wc -l < "$f" 2>/dev/null)
    printf '  [有] %-44s %8s 字节  %5s 行\n' "$f" "$SZ" "$LN"
  else
    printf '  [无] %s\n' "$f"
  fi
done

echo
echo "########## D. show_uid_stat 样例 ##########"
if [ -r /proc/uid_cputime/show_uid_stat ]; then
  head -n 6 /proc/uid_cputime/show_uid_stat | sed 's/^/    /'
  echo "    行数=$(wc -l < /proc/uid_cputime/show_uid_stat)"
fi

echo
echo "########## E. uid_io 样例 ##########"
if [ -r /proc/uid_io/stats ]; then
  head -n 4 /proc/uid_io/stats | sed 's/^/    /'
  echo "    行数=$(wc -l < /proc/uid_io/stats)"
fi

echo
echo "########## F. 当前 PowerLens 进程 ##########"
PID=$(ps -A -o PID,NAME 2>/dev/null | awk '$2=="com.powerlens.app"{print $1}' | head -n1)
if [ -n "$PID" ]; then
  echo "  PID=$PID"
  grep -E 'VmRSS|VmSwap|Threads' /proc/$PID/status | sed 's/^/    /'
  S1=$(awk '{print $14+$15}' /proc/$PID/stat 2>/dev/null)
  sleep 10
  S2=$(awk '{print $14+$15}' /proc/$PID/stat 2>/dev/null)
  echo "    10s CPU tick: $((S2 - S1))  (= $(( (S2-S1) * 10 )) ms)"
else
  echo "  app 未运行"
fi

echo
echo "########## G. JobScheduler 执行历史 ##########"
dumpsys jobscheduler 2>/dev/null | grep -A6 'com.powerlens' | head -n 20 | sed 's/^/  /'