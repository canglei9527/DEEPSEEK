#!/system/bin/sh
# screen-on-audit.sh -- 亮屏静止功耗分析（找出跳变的来源）
#
# 目标：0.5W ≈ 130 mA @ 3.85V；实测会跳到 1W ≈ 260 mA
#
# 排查方向：
#   1. 瞬时空闲电流分布（采样 1 秒一次，看跳动幅度和周期）
#   2. 亮屏期间谁在吃 CPU（per-UID 差值）
#   3. 屏幕亮度、刷新率、GPU 状态
#   4. 是否有周期性唤醒（alarm/wakelock）
#   5. 调制解调器/WiFi 的周期性活动
#
# 用法: su -c "sh /data/local/tmp/screen-on-audit.sh [秒数，默认120]"

WIN=${1:-120}
LOG=/data/local/tmp/screen-on-audit.log
: > $LOG

echo "===== 亮屏静止功耗分析 $(date '+%F %T') ${WIN}s =====" >> $LOG

# ---- 前提 ----
WAKE=$(dumpsys power 2>/dev/null | grep -o 'mWakefulness=[A-Za-z]*')
BL=$(cat /sys/class/backlight/panel0-backlight/brightness 2>/dev/null)
ST=$(cat /sys/class/power_supply/battery/status 2>/dev/null)
echo "屏幕: $WAKE" >> $LOG
echo "背光: ${BL:-不可读}" >> $LOG
echo "电池: $ST" >> $LOG

# ---- 1. 电流采样（1 秒一次，用库仑计差值算平均电流）----
echo "" >> $LOG
echo "===== 1. 电流时间序列（每 2 秒，用 charge_counter 差值）=====" >> $LOG
C0=$(cat /sys/class/power_supply/battery/charge_counter 2>/dev/null)
T0=$(date +%s)
PREV_C=$C0
PREV_T=$T0
i=0
MAX=0
MIN=999999
SUM=0
CNT=0
while [ $i -lt $WIN ]; do
  sleep 2
  C=$(cat /sys/class/power_supply/battery/charge_counter 2>/dev/null)
  T=$(date +%s)
  DT=$((T - PREV_T))
  if [ "$DT" -gt 0 ] && [ -n "$C" ] && [ "$C" -gt 0 ]; then
    MA=$(awk -v a=$PREV_C -v b=$C -v t=$DT 'BEGIN{printf "%.0f", (a-b)/1000*3600/t}')
    printf '  %s  %s mA\n' "$(date '+%H:%M:%S')" "$MA" >> $LOG
    [ "$MA" -gt "$MAX" ] && MAX=$MA
    [ "$MA" -lt "$MIN" ] && MIN=$MA
    SUM=$((SUM + MA))
    CNT=$((CNT + 1))
  fi
  PREV_C=$C
  PREV_T=$T
  i=$((i + 2))
done

echo "" >> $LOG
awk -v mx=$MAX -v mn=$MIN -v s=$SUM -v n=$CNT 'BEGIN{
  if (n > 0) {
    printf "  平均 %.0f mA   最小 %d mA   最大 %d mA\n", s/n, mn, mx
    printf "  按 3.85V 折算: 平均 %.2f W   最大 %.2f W\n", s/n*3.85/1000, mx*3.85/1000
    if (mx > (s/n)*1.5) printf "  [注意] 峰值超过均值 50%%，存在明显跳变\n"
  } else print "  (无有效采样)"
}' >> $LOG

# ---- 2. 亮屏期间 per-UID CPU 差值 ----
echo "" >> $LOG
echo "===== 2. 亮屏期间 CPU 消耗（60s 差值，top 12）=====" >> $LOG
sed 's/: / /' /proc/uid_cputime/show_uid_stat 2>/dev/null | awk '{print $1, $2+$3}' | sort > /tmp/sc0
sleep 60
sed 's/: / /' /proc/uid_cputime/show_uid_stat 2>/dev/null | awk '{print $1, $2+$3}' | sort > /tmp/sc1
awk 'FNR==NR{p[$1]=$2;next}{if($1 in p){d=$2-p[$1]; if(d>200000) printf "%d %d\n",$1,d}}' /tmp/sc0 /tmp/sc1 \
  | sort -k2 -nr | head -n 12 | while read uid d; do
      pkg=$(awk -v u="$uid" '$2==u{print $1; exit}' /data/system/packages.list 2>/dev/null)
      [ -z "$pkg" ] && case $uid in 0) pkg="root/内核";; 1000) pkg="system_server";; 2000) pkg="adb shell";; *) pkg="uid$uid";; esac
      printf '  %-42s %6d ms (%.0f%% 单核)\n' "$pkg" "$((d/1000))" "$(awk -v d=$d 'BEGIN{printf "%.1f", d/1000000*100/1}')" >> $LOG
    done

# ---- 3. 显示与 GPU 状态 ----
echo "" >> $LOG
echo "===== 3. 显示状态 =====" >> $LOG
dumpsys display 2>/dev/null | grep -iE 'mScreenState|mRefreshRate|fps|mBaseDisplayInfo' | head -n 6 | sed 's/^/  /' >> $LOG
echo "  --- GPU 频率 ---" >> $LOG
for f in /sys/class/kgsl/kgsl-3d0/gpuclk /sys/class/kgsl/kgsl-3d0/devfreq/cur_freq /sys/class/kgsl/kgsl-3d0/gpu_busy_percentage; do
  [ -r "$f" ] && printf '  %-52s %s\n' "$(basename $f)" "$(cat $f 2>/dev/null)" >> $LOG
done
echo "  --- CPU 频率 ---" >> $LOG
for c in 0 4 7; do
  f=/sys/devices/system/cpu/cpu$c/cpufreq/scaling_cur_freq
  [ -r "$f" ] && printf '  cpu%d %s kHz\n' "$c" "$(cat $f 2>/dev/null)" >> $LOG
done

# ---- 4. 唤醒锁与 Alarm ----
echo "" >> $LOG
echo "===== 4. 唤醒锁（亮屏期间）=====" >> $LOG
dumpsys power 2>/dev/null | sed -n '/Wake Locks/,/^$/p' | head -n 12 | sed 's/^/  /' >> $LOG
echo "  --- 用户态 wake_lock ---" >> $LOG
cat /sys/power/wake_lock 2>/dev/null | sed 's/^/  /' >> $LOG

echo "" >> $LOG
echo "===== 5. 高频 Alarm（前 8）=====" >> $LOG
dumpsys alarm 2>/dev/null | grep -A2 'Top Alarms' | head -n 12 | sed 's/^/  /' >> $LOG

# ---- 6. 网络活动 ----
echo "" >> $LOG
echo "===== 6. 网络活动（60s）=====" >> $LOG
R0=$(cat /sys/class/net/wlan0/statistics/rx_packets 2>/dev/null)
T0=$(cat /sys/class/net/wlan0/statistics/tx_packets 2>/dev/null)
sleep 60
R1=$(cat /sys/class/net/wlan0/statistics/rx_packets 2>/dev/null)
T1=$(cat /sys/class/net/wlan0/statistics/tx_packets 2>/dev/null)
awk -v r0=$R0 -v r1=$R1 -v t0=$T0 -v t1=$T1 'BEGIN{
  printf "  wlan0: 收 %d 包/分  发 %d 包/分\n", r1-r0, t1-t0
}' >> $LOG

echo "" >> $LOG
echo "===== 结论指引 =====" >> $LOG
echo "  目标 0.5W ≈ 130 mA。查三处：" >> $LOG
echo "    ① 第1节：峰值 vs 均值 —— 差多少就是跳变幅度" >> $LOG
echo "    ② 第2节：跳变时谁在吃 CPU（对比「全部口径」的排行）" >> $LOG
echo "    ③ 第4/5节：是否周期性唤醒（alarm 特别看 1000:android）" >> $LOG

cat $LOG