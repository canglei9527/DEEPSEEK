#!/system/bin/sh
# delta2.sh -- per-UID CPU 差值（避开 busybox join 的 process substitution 限制）
# 用法: su -c "sh /data/local/tmp/delta2.sh 60"

GAP=${1:-60}
D=/data/local/tmp/plc
mkdir -p $D

cat /proc/uid_cputime/show_uid_stat > $D/c0.txt
cat /proc/uid_io/stats > $D/io0.txt
C0=$(cat /sys/class/power_supply/battery/charge_counter)
T0=$(date +%s)
S0=$(cat /sys/power/suspend_stats/success)
F0=$(cat /sys/power/suspend_stats/fail)
E0=$(dmesg | grep -c 'PM: suspend entry')
A0=$(dmesg | grep -c 'Abort: Pending Wakeup Sources')
W0=$(dumpsys power 2>/dev/null | grep -o 'mWakefulness=[A-Za-z]*')

echo "等待 ${GAP}s ..."
sleep $GAP

cat /proc/uid_cputime/show_uid_stat > $D/c1.txt
cat /proc/uid_io/stats > $D/io1.txt
C1=$(cat /sys/class/power_supply/battery/charge_counter)
T1=$(date +%s)
S1=$(cat /sys/power/suspend_stats/success)
F1=$(cat /sys/power/suspend_stats/fail)
E1=$(dmesg | grep -c 'PM: suspend entry')
A1=$(dmesg | grep -c 'Abort: Pending Wakeup Sources')
W1=$(dumpsys power 2>/dev/null | grep -o 'mWakefulness=[A-Za-z]*')

DT=$((T1 - T0)); [ $DT -le 0 ] && DT=$GAP

echo
echo "########## 区间总览（${DT}s）##########"
echo "  电量计: $C0 -> $C1  ($(( (C0-C1)/1000 )) mAh)"
awk -v a=$C0 -v b=$C1 -v t=$DT 'BEGIN{printf "  平均电流: %.1f mA\n", (a-b)/1000*3600/t}'
echo "  屏幕: $W0 -> $W1"
echo "  挂起成功 +$((S1-S0))  失败 +$((F1-F0))  entry +$((E1-E0))  abort +$((A1-A0))"

# 统一成 "uid value" 两列，再合并求差
sed 's/: / /' $D/c0.txt | awk '{print $1, $2+$3}' | sort -k1,1n > $D/a0.txt
sed 's/: / /' $D/c1.txt | awk '{print $1, $2+$3}' | sort -k1,1n > $D/a1.txt
join -j 1 $D/a0.txt $D/a1.txt 2>/dev/null | awk -v dt=$DT '{
  d = $3 - $2
  if (d > 0) printf "%d %d %d\n", $1, d, d*100/(dt*1000000)
}' | sort -k2,2nr > $D/delta.txt

TOTAL=$(awk '{s+=$2} END{print s+0}' $D/delta.txt)
COUNT=$(wc -l < $D/delta.txt)

echo
echo "########## CPU 增量 top 20 ##########"
head -n 20 $D/delta.txt | while read uid d pct; do
  pkg=""
  [ -r /data/system/packages.list ] && pkg=$(awk -v u="$uid" '$2==u{print $1; exit}' /data/system/packages.list)
  if [ -z "$pkg" ]; then
    case $uid in
      0) pkg="root/kernel" ;;
      1000) pkg="system_server" ;;
      1001) pkg="radio" ;;
      1013) pkg="media" ;;
      1047) pkg="wifi" ;;
      *) pkg="(uid $uid)" ;;
    esac
  fi
  printf '  %-44s %7d ms  %4d%%\n' "$pkg" "$((d/1000))" "$pct"
done

echo
echo "########## 汇总 ##########"
awk -v t="$TOTAL" -v n="$COUNT" -v dt=$DT 'BEGIN{
  printf "  %d 个 UID 有增量，合计 %.1f 秒 = 单核 %.0f%%\n", n, t/1000000, t/(dt*1000000)*100
}'

echo
echo "########## 进程/线程 ##########"
P=$(ls -d /proc/[0-9]* 2>/dev/null | wc -l)
echo "  processes=$P"
echo "  loadavg: $(cat /proc/loadavg)"

echo
echo "########## 系统 CPU 分布（/proc/stat 差值）##########"
read _ u1 n1 s1 i1 w1 q1 sq1 st1 < /proc/stat
sleep 5
read _ u2 n2 s2 i2 w2 q2 sq2 st2 < /proc/stat
awk -v u1=$u1 -v n1=$n1 -v s1=$s1 -v i1=$i1 -v w1=$w1 -v q1=$q1 -v sq1=$sq1 -v st1=$st1 \
    -v u2=$u2 -v n2=$n2 -v s2=$s2 -v i2=$i2 -v w2=$w2 -v q2=$q2 -v sq2=$sq2 -v st2=$st2 'BEGIN{
  du=u2-u1; dn=n2-n1; ds=s2-s1; di=i2-i1; dw=w2-w1; dq=q2-q1; dsq=sq2-sq1; dst=st2-st1
  tot=du+dn+ds+di+dw+dq+dsq+dst
  if (tot>0) {
    printf "  user=%.1f%% nice=%.1f%% sys=%.1f%% idle=%.1f%% iowait=%.1f%% irq=%.1f%% softirq=%.1f%% steal=%.1f%%\n",
      du*100/tot, dn*100/tot, ds*100/tot, di*100/tot, dw*100/tot, dq*100/tot, dsq*100/tot, dst*100/tot
    printf "  => 非 idle 合计 %.1f%%（8 核），单核等效 %.1f%%\n", (tot-di)*100/tot, (tot-di)*100/tot/8
  }
}'