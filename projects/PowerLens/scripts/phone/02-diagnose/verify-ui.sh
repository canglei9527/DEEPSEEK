#!/system/bin/sh
# verify-ui.sh -- 核对 App 显示的数字与系统实测是否一致
# 用法: su -c "sh /data/local/tmp/verify-ui.sh"

F=/data/data/com.powerlens.app/files/snapshots.tsv
D=/data/local/tmp/plc

echo "########## 1. 电量核算核对 ##########"
"$D/plcollect" --status -f "$F" 2>&1 | sed 's/^/  /'

echo
echo "########## 2. App 显示的是 878.0 mAh / 318.2 mA / 2.8 小时 ##########"
echo "  核对: 878.0 / 2.8 = $(awk 'BEGIN{printf "%.1f", 878.0/2.8}') mA"
echo "  如果接近 318.2，说明电流算法正确"

echo
echo "########## 3. 当前真实电流（独立测量 3 分钟）##########"
C0=$(cat /sys/class/power_supply/battery/charge_counter)
T0=$(date +%s)
sleep 180
C1=$(cat /sys/class/power_supply/battery/charge_counter)
T1=$(date +%s)
DT=$((T1-T0))
awk -v a=$C0 -v b=$C1 -v t=$DT 'BEGIN{
  printf "  实测电流: %.1f mA  (%.1f mAh / %.1f 分钟)\n", (a-b)/1000*3600/t, (a-b)/1000, t/60
}'
echo "  屏幕: $(dumpsys power 2>/dev/null | grep -o 'mWakefulness=[A-Za-z]*')"

echo
echo "########## 4. 按 UID 的真实 CPU 占比（procfs 直接算）##########"
cat /proc/uid_cputime/show_uid_stat | sed 's/: / /' | awk '{print $1, $2+$3}' | sort -k1,1n > /tmp/u0
sleep 60
cat /proc/uid_cputime/show_uid_stat | sed 's/: / /' | awk '{print $1, $2+$3}' | sort -k1,1n > /tmp/u1
awk 'FNR==NR{p[$1]=$2;next}{if($1 in p){d=$2-p[$1]; if(d>0) printf "%d %d\n",$1,d}}' /tmp/u0 /tmp/u1 \
  | sort -k2 -nr | head -n 10 | while read uid d; do
      pkg=""
      [ -r /data/system/packages.list ] && pkg=$(awk -v u="$uid" '$2==u{print $1; exit}' /data/system/packages.list)
      [ -z "$pkg" ] && pkg="uid$uid"
      printf '  %-46s %6d ms\n' "$pkg" "$((d/1000))"
    done

echo
echo "########## 5. 结论 ##########"
echo "  如果第4节的排序和 App 列表大致一致，说明 App 的 CPU 归因是对的"
echo "  如果电流 318 mA 持续存在，说明当前确实在耗电（可能是亮屏/充电）"