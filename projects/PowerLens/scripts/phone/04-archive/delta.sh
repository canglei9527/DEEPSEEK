#!/system/bin/sh
# delta.sh -- 两次 procfs 采样取差值，算真实的 per-UID CPU 消耗
# 用法: su -c "sh /data/local/tmp/delta.sh 60"
#   $1 = 采样间隔秒（默认 60）
#
# 为什么必须做差值：
#   /proc/uid_cputime/show_uid_stat 是**开机以来的累加值**，
#   单次快照看到的 6 亿秒是历史总量，不是当前消耗。
#   只有差值才能回答"现在谁在耗电"。

GAP=${1:-60}
CPU0=/data/local/tmp/plc/d_cpu0.txt
CPU1=/data/local/tmp/plc/d_cpu1.txt
IO0=/data/local/tmp/plc/d_io0.txt
IO1=/data/local/tmp/plc/d_io1.txt
mkdir -p /data/local/tmp/plc

echo "== 采样 T0 =="
cat /proc/uid_cputime/show_uid_stat > $CPU0
cat /proc/uid_io/stats > $IO0
C0=$(cat /sys/class/power_supply/battery/charge_counter)
T0=$(date +%s)
S0=$(cat /sys/power/suspend_stats/success)
E0=$(dmesg | grep -c 'PM: suspend entry')

echo "  等待 ${GAP}s ..."
sleep $GAP

echo "== 采样 T1 =="
cat /proc/uid_cputime/show_uid_stat > $CPU1
cat /proc/uid_io/stats > $IO1
C1=$(cat /sys/class/power_supply/battery/charge_counter)
T1=$(date +%s)
S1=$(cat /sys/power/suspend_stats/success)
E1=$(dmesg | grep -c 'PM: suspend entry')

DT=$((T1 - T0))
[ $DT -le 0 ] && DT=$GAP

echo
echo "########## 区间总览（${DT}s）##########"
echo "  电量计: $C0 -> $C1  (= $(( (C0-C1)/1000 )) mAh)"
if [ $DT -gt 0 ]; then
  echo "  平均电流: $(awk "BEGIN{printf \"%.1f\", ($C0-$C1)/1000*3600/$DT}") mA"
fi
echo "  挂起成功: +$((S1-S0))  entry: +$((E1-E0))"
echo "  屏幕: $(dumpsys power 2>/dev/null | grep -o 'mWakefulness=[A-Za-z]*')"

echo
echo "########## CPU 增量 top 20（µs -> ms，含每核占比）##########"
# 两列格式 "<uid>: <utime> <stime>"，join 后求差
join -t: -1 1 -2 1 \
  <(sed 's/: /|/' $CPU0 | awk -F'|' '{split($2,a," "); print $1"|"a[1]"|"a[2]}' | sort -t'|' -k1,1) \
  <(sed 's/: /|/' $CPU1 | awk -F'|' '{split($2,a," "); print $1"|"a[1]"|"a[2]}' | sort -t'|' -k1,1) \
  2>/dev/null | awk -F'|' -v dt="$DT" '
{
  uid=$1
  d = ($4+$5) - ($2+$3)        # 总增量 µs
  if (d <= 0) next
  ms = d/1000
  pct = d / (dt * 1000000) * 100   # 单核百分比
  printf "%d\t%.0f\t%.1f\t%d\n", uid, ms, pct, d
}' | sort -k2 -nr | head -n 20 | while IFS="$(printf '\t')" read uid ms pct raw; do
  name=$(awk -F'\t' -v u="$uid" '$1==u{print $2; exit}' $IO1 2>/dev/null)
  nm=$(tr -d '\000' < /proc/$uid/cmdline 2>/dev/null | head -c 40)
  [ -z "$nm" ] && nm=$(cat /proc/$uid/comm 2>/dev/null)
  # 用 uid 反查包名（从 /data/system/packages.list）
  pkg=""
  if [ -r /data/system/packages.list ]; then
    pkg=$(awk -v u="$uid" '$2==u{print $1; exit}' /data/system/packages.list)
  fi
  [ -z "$pkg" ] && pkg="$nm"
  [ -z "$pkg" ] && pkg="(uid $uid)"
  printf '  %-42s %8s ms  %5s%%\n' "$pkg" "$ms" "$pct"
done

echo
echo "########## 前 20 名合计占比 ##########"
join -t'|' \
  <(sed 's/: /|/' $CPU0 | awk -F'|' '{split($2,a," "); print $1"|"a[1]+a[2]}' | sort -t'|' -k1,1) \
  <(sed 's: :|:' $CPU1 | awk -F'|' '{split($2,a," "); print $1"|"a[1]+a[2]}' | sort -t'|' -k1,1) \
  2>/dev/null | awk -F'|' -v dt="$DT" '
{ d = $3 - $2; if (d > 0) { tot += d; n++ } }
END { printf "  %d 个 UID 有 CPU 增量，总计 %.1f 秒 ≈ 单核 %.0f%%\n", n, tot/1000000, tot/(dt*1000000)*100 }'

echo
echo "########## 进程/线程总数（崩溃/泄漏检测）##########"
P=$(ls -d /proc/[0-9]* 2>/dev/null | wc -l)
T=0
for d in /proc/[0-9]*; do
  set -- "$d"/task/*
  T=$((T + $#))
done
echo "  processes=$P  threads=$T"
echo "  loadavg: $(cat /proc/loadavg)"