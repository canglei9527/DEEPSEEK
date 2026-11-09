#!/system/bin/sh
# swtune2.sh -- swappiness A/B（修正版：字段对齐、计时可靠）
# 用法: su -c "sh /data/local/tmp/swtune2.sh"

SW=/proc/sys/vm/swappiness
WF=/proc/sys/vm/watermark_scale_factor
ORIG_SW=$(cat $SW)
ORIG_WF=$(cat $WF)
GAP=45

echo "原始: swappiness=$ORIG_SW  watermark_scale_factor=$ORIG_WF"

measure() {
  label=$1

  # 采样前的值
  P0=$(awk '/^pswpin /{print $2}' /proc/vmstat)
  O0=$(awk '/^pswpout /{print $2}' /proc/vmstat)
  M0=$(awk '/^pgmajfault /{print $2}' /proc/vmstat)
  J0=$(awk '/^cpu /{print $5+$6}' /proc/stat)
  T0=$(awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8+$9}' /proc/stat)
  C0=$(cat /sys/class/power_supply/battery/charge_counter)
  E0=$(date +%s)

  sleep $GAP

  P1=$(awk '/^pswpin /{print $2}' /proc/vmstat)
  O1=$(awk '/^pswpout /{print $2}' /proc/vmstat)
  M1=$(awk '/^pgmajfault /{print $2}' /proc/vmstat)
  J1=$(awk '/^cpu /{print $5+$6}' /proc/stat)
  T1=$(awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8+$9}' /proc/stat)
  C1=$(cat /sys/class/power_supply/battery/charge_counter)
  E1=$(date +%s)

  DT=$((E1 - E0))
  [ "$DT" -lt 1 ] && DT=$GAP

  awk -v lbl="$label" -v dt=$DT \
      -v p0=$P0 -v p1=$P1 -v o0=$O0 -v o1=$O1 -v m0=$M0 -v m1=$M1 \
      -v j0=$J0 -v j1=$J1 -v t0=$T0 -v t1=$T1 -v c0=$C0 -v c1=$C1 'BEGIN{
    dtj = t1 - t0
    if (dtj <= 0) dtj = dt * 800
    printf "  [%s] %d 秒\n", lbl, dt
    printf "    pswpin   %8d 页  (%6.1f 页/秒)\n", p1-p0, (p1-p0)/dt
    printf "    pswpout  %8d 页  (%6.1f 页/秒)\n", o1-o0, (o1-o0)/dt
    printf "    majfault %8d 次  (%6.1f 次/秒)\n", m1-m0, (m1-m0)/dt
    printf "    iowait   %8.1f%%  (jiffies %d / %d)\n", (j1-j0)*100/dtj, j1-j0, dtj
    printf "    电流     %8.1f mA\n", (c0-c1)/1000*3600/dt
  }'
}

echo
echo "===== A. 基线 swappiness=$ORIG_SW ====="
measure "基线"

echo
echo "===== B. swappiness=60 ====="
echo 60 > $SW
sleep 8
measure "sw=60"

echo
echo "===== C. sw=60 + watermark_scale_factor=150 ====="
echo 150 > $WF
sleep 8
measure "sw=60+wsf150"

echo
echo "===== 还原 ====="
echo $ORIG_WF > $WF
echo $ORIG_SW > $SW
echo "  swappiness=$(cat $SW)  watermark_scale_factor=$(cat $WF)"