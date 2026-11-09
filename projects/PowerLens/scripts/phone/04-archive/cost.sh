#!/system/bin/sh
# cost.sh -- 量化 PowerLens 自身的开销，找出真正的成本中心
# 用法: su -c "sh /data/local/tmp/cost.sh"

echo "########## A. dumpsys batterystats --charged 的真实成本 ##########"
T0=$(date +%s%N)
su -c 'dumpsys batterystats --charged' > /data/local/tmp/_ds1.txt 2>/dev/null
T1=$(date +%s%N)
echo "  耗时: $(( (T1 - T0) / 1000000 )) ms"
echo "  输出: $(wc -c < /data/local/tmp/_ds1.txt) 字节"

echo
echo "  --- dumpsys 进程自身 CPU（第二次，取 timer 值）---"
T0=$(date +%s%N)
su -c 'dumpsys batterystats --charged' > /data/local/tmp/_ds2.txt 2>/dev/null
T1=$(date +%s%N)
echo "  耗时: $(( (T1 - T0) / 1000000 )) ms"
# 用 /proc/stat 全系统 CPU 增量估算
read _ a1 b1 c1 d1 e1 f1 g1 h1 < /proc/stat
su -c 'dumpsys batterystats --charged' > /dev/null 2>&1
read _ a2 b2 c2 d2 e2 f2 g2 h2 < /proc/stat
TOT1=$((a1+b1+c1+d1+e1+f1+g1+h1)); TOT2=$((a2+b2+c2+d2+e2+f2+g2+h2))
IDLE1=$((d1+e1)); IDLE2=$((d2+e2))
DT=$((TOT2-TOT1)); DI=$((IDLE2-IDLE1))
[ "$DT" -gt 0 ] && echo "  系统 CPU 占用: $(( (DT - DI) * 100 / DT ))%（8 核合计，采样窗口）"

echo
echo "########## B. PowerLens app 进程成本 ##########"
PID=$(ps -A -o PID,NAME 2>/dev/null | awk '$2=="com.powerlens.app"{print $1}')
if [ -n "$PID" ]; then
  echo "  PID=$PID"
  grep -E 'VmRSS|VmSwap|Threads' /proc/$PID/status | sed 's/^/    /'
  S1=$(awk '{print $14+$15}' /proc/$PID/stat)
  sleep 10
  S2=$(awk '{print $14+$15}' /proc/$PID/stat)
  echo "    10 秒 CPU tick 增量: $((S2 - S1))  (1 tick = 10ms，即 $(( (S2-S1) * 10 )) ms)"
else
  echo "  app 未运行（后台已回收）"
fi

echo
echo "########## C. 更便宜的 per-UID CPU 数据源是否存在 ##########"
for f in /proc/uid_cputime/show_uid_stat \
         /proc/uid_cputime/remove_uid_range \
         /proc/uid_io/stats \
         /proc/uid_procstat/set \
         /sys/fs/bpf/map_uid_system_cpu_time \
         /sys/fs/bpf/map_uid_time_in_state; do
  if [ -e "$f" ]; then
    printf '  [有] %-46s %s 字节\n' "$f" "$(wc -c < $f 2>/dev/null)"
  else
    printf '  [无] %s\n' "$f"
  fi
done

echo
echo "  --- /proc/uid_cputime/show_uid_stat 前 5 行 + 行数 ---"
if [ -r /proc/uid_cputime/show_uid_stat ]; then
  head -n 5 /proc/uid_cputime/show_uid_stat | sed 's/^/    /'
  echo "    总行数: $(wc -l < /proc/uid_cputime/show_uid_stat)"
fi

echo
echo "########## D. 读取成本对比（各跑 20 次取平均） ##########"
bench() {
  name=$1; cmd=$2
  t0=$(date +%s%N)
  i=0
  while [ $i -lt 20 ]; do
    sh -c "$cmd" > /dev/null 2>&1
    i=$((i+1))
  done
  t1=$(date +%s%N)
  echo "  $name: $(( (t1-t0) / 20000000 )) ms/次"
}
bench "cat /proc/uid_cputime/show_uid_stat" "cat /proc/uid_cputime/show_uid_stat"
bench "cat /proc/uid_io/stats" "cat /proc/uid_io/stats"
bench "cat /sys/class/power_supply/battery/charge_counter" "cat /sys/class/power_supply/battery/charge_counter"
bench "cat /proc/meminfo" "cat /proc/meminfo"

echo
echo "  --- dumpsys 只跑 3 次取平均（太慢） ---"
t0=$(date +%s%N)
i=0
while [ $i -lt 3 ]; do
  dumpsys batterystats --charged > /dev/null 2>&1
  i=$((i+1))
done
t1=$(date +%s%N)
echo "  dumpsys batterystats --charged: $(( (t1-t0) / 3000000 )) ms/次"

echo
echo "########## E. 更轻的 dumpsys 变体 ##########"
for v in "--charged" "" "--proto" "--uid" "--checkin"; do
  t0=$(date +%s%N)
  SZ=$(dumpsys batterystats $v 2>/dev/null | wc -c)
  t1=$(date +%s%N)
  printf '  dumpsys batterystats %-10s  %8s 字节  %5s ms\n' "$v" "$SZ" "$(( (t1-t0) / 1000000 ))"
done

rm -f /data/local/tmp/_ds1.txt /data/local/tmp/_ds2.txt