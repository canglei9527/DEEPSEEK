#!/system/bin/sh
# swtune.sh -- 判断 swappiness / watermark 调整是否有效（可逆，测试完自动还原）
#
# 假设：
#   1) swappiness=100 让内核把活跃匿名页也换出去，但它们马上要被换回来
#      → pswpin 7041 页/秒 vs pswpout 3175 页/秒，换入比换出多一倍
#      → 这是典型的 "thrashing"（来回倒）
#   2) 降到 60~80 应该让内核优先丢 page cache，而不是换出匿名页
#
# 用法: su -c "sh /data/local/tmp/swtune.sh"

SW=/proc/sys/vm/swappiness
ORIG=$(cat $SW)
echo "== 原始 swappiness = $ORIG =="

measure() {
  label=$1
  a=$(grep -E '^pswpin |^pswpout |^pgmajfault ' /proc/vmstat | awk '{print $2}' | tr '\n' ' ')
  s0=$(awk '/^cpu /{print $5+$6}' /proc/stat)
  t0=$(cat /sys/class/power_supply/battery/charge_counter)
  T0=$(date +%s)
  sleep 40
  b=$(grep -E '^pswpin |^pswpout |^pgmajfault ' /proc/vmstat | awk '{print $2}' | tr '\n' ' ')
  s1=$(awk '/^cpu /{print $5+$6}' /proc/stat)
  t1=$(cat /sys/class/power_supply/battery/charge_counter)
  T1=$(date +%s)
  DT=$((T1-T0))
  echo "$a $b $s0 $s1 $t0 $t1 $DT" | awk -v lbl="$label" '{
    printf "  [%s] %ds\n", lbl, $7
    printf "    pswpin  = %6d (%.0f 页/秒)\n", $4-$1, ($4-$1)/$7
    printf "    pswpout = %6d (%.0f 页/秒)\n", $5-$2, ($5-$2)/$7
    printf "    majfault= %6d (%.0f 次/秒)\n", $6-$3, ($6-$3)/$7
    printf "    iowait  = %.1f%%  (总 jiffies %d)\n", ($9-$8)*100/($7*800), $9-$8
    printf "    电流    = %.1f mA\n", ($10-$11)/1000*3600/$7
  }'
}

echo
echo "########## A. 基线（swappiness=$ORIG） ##########"
measure "sw=$ORIG"

echo
echo "########## B. swappiness=60 ##########"
echo 60 > $SW
echo "  已设为 $(cat $SW)，等 10 s 让内核调整"
sleep 10
measure "sw=60"

echo
echo "########## C. 同时提高 watermark_scale_factor（更早回收，减少 direct reclaim） ##########"
WF=/proc/sys/vm/watermark_scale_factor
OWF=$(cat $WF)
echo 150 > $WF
echo "  sw=$(cat $SW)  wsf=$(cat $WF)"
sleep 10
measure "sw=60 wsf=150"

echo
echo "########## D. 还原 ##########"
echo $OWF > $WF
echo $ORIG > $SW
echo "  swappiness=$(cat $SW)  watermark_scale_factor=$(cat $WF)"

echo
echo "########## 判读 ##########"
echo "  看 pswpin 是否显著下降（说明不再来回倒换）"
echo "  看 iowait 是否下降（说明 direct reclaim 减少）"
echo "  若 B 比 A 好 → 固化 swappiness=60 到 service.d"
echo "  若 C 比 B 更好 → 一并固化 watermark_scale_factor=150"