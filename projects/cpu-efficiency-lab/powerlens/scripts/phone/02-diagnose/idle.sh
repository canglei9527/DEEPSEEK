#!/system/bin/sh
# idle.sh -- 验证 "iowait 86% 是假的"
#
# 判据：
#   真正的 I/O 阻塞 → PSI io some/full 应同步升高
#   假 iowait（idle 状态被误记）→ PSI io 低，且 cpuidle 各状态时间异常
#
# 用法: su -c "sh /data/local/tmp/idle.sh"

echo "########## 1. /proc/stat 逐核 20 秒差值（idle vs iowait 精确分解） ##########"
snap() {
  awk '/^cpu[0-9] /{
    # $2 user $3 nice $4 sys $5 idle $6 iowait $7 irq $8 softirq $9 steal
    printf "%s %d %d %d %d %d %d %d %d\n", $1, $2,$3,$4,$5,$6,$7,$8,$9
  }' /proc/stat
}
snap > /tmp/st0
sleep 20
snap > /tmp/st1

awk '
FNR==NR { for(i=2;i<=9;i++) a[$1,i]=$i; next }
{
  d2=$2-a[$1,2]; d3=$3-a[$1,3]; d4=$4-a[$1,4]; d5=$5-a[$1,5]
  d6=$6-a[$1,6]; d7=$7-a[$1,7]; d8=$8-a[$1,8]; d9=$9-a[$1,9]
  tot=d2+d3+d4+d5+d6+d7+d8+d9
  if (tot>0) printf "  %-6s user=%4.1f%% sys=%4.1f%% idle=%4.1f%% iowait=%5.1f%% irq=%3.1f%% sirq=%3.1f%%\n",
    $1, d2*100/tot, d4*100/tot, d5*100/tot, d6*100/tot, d7*100/tot, d8*100/tot
}' /tmp/st0 /tmp/st1

echo
echo "########## 2. PSI（真实阻塞压力） ##########"
for f in cpu io memory; do
  echo "  --- $f ---"
  cat /proc/pressure/$f 2>/dev/null | sed 's/^/    /'
done

echo
echo "########## 3. cpuidle 各状态驻留时间（关键） ##########"
for c in 0 4 7; do
  echo "  --- cpu$c ---"
  drv=$(cat /sys/devices/system/cpu/cpu$c/cpuidle/current_driver 2>/dev/null)
  echo "    driver=$drv"
  g=$(cat /sys/devices/system/cpu/cpu$c/cpuidle/current_governor 2>/dev/null)
  echo "    governor=$g"
  for s in /sys/devices/system/cpu/cpu$c/cpuidle/state*/; do
    [ -r "$s/name" ] || continue
    n=$(cat $s/name 2>/dev/null)
    t=$(cat $s/time 2>/dev/null)
    u=$(cat $s/usage 2>/dev/null)
    d=$(cat $s/disable 2>/dev/null)
    r=$(cat $s/residency 2>/dev/null)
    printf '    %-12s usage=%-10s time=%-14s residency=%-8s disable=%s\n' "$n" "$u" "$t" "$r" "$d"
  done
done

echo
echo "########## 4. cpuidle 20 秒增量（看进的是哪个状态） ##########"
for c in 0 7; do
  echo "  --- cpu$c ---"
  for s in /sys/devices/system/cpu/cpu$c/cpuidle/state*/; do
    [ -r "$s/time" ] || continue
    echo "$(cat $s/name) $(cat $s/time) $(cat $s/usage)"
  done > /tmp/ci${c}_0
done
sleep 20
for c in 0 7; do
  echo "  --- cpu$c (20s 增量) ---"
  for s in /sys/devices/system/cpu/cpu$c/cpuidle/state*/; do
    [ -r "$s/time" ] || continue
    echo "$(cat $s/name) $(cat $s/time) $(cat $s/usage)"
  done > /tmp/ci${c}_1
  awk -v cpu=$c '
    FNR==NR { t0[$1]=$2; u0[$1]=$3; next }
    {
      dt = ($2 - t0[$1]) / 1000000     # us -> s
      du = $3 - u0[$1]
      if (du > 0) printf "    %-12s 驻留 %6.2f 秒  %6d 次  平均 %.1f ms/次\n", $1, dt, du, dt*1000/du
      else printf "    %-12s 本窗口未进入\n", $1
    }' /tmp/ci${c}_0 /tmp/ci${c}_1
done

echo
echo "########## 5. 挂起统计（同时段） ##########"
python() { :; }
E0=$(dmesg | grep -c 'PM: suspend entry')
A0=$(dmesg | grep -c 'Abort: Pending')
S0=$(cat /sys/power/suspend_stats/success)
sleep 20
E1=$(dmesg | grep -c 'PM: suspend entry')
A1=$(dmesg | grep -c 'Abort: Pending')
S1=$(cat /sys/power/suspend_stats/success)
echo "  20 秒内: suspend entry +$((E1-E0))  abort +$((A1-A0))  success +$((S1-S0))"

echo
echo "########## 6. 判定 ##########"
echo "  如果 iowait 高但 PSI io 低 => iowait 是假的（idle 误记），真问题不是磁盘"
echo "  如果 CPU 大量驻留在 state0/state1 而不进深睡 => 被频繁唤醒（timerfd/s2idle 循环）"
echo "  如果 suspend entry 次数 ≈ abort 次数 => 挂起从未成功"