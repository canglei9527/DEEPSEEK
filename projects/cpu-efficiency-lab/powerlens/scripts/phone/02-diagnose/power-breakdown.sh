#!/system/bin/sh
# power-breakdown.sh -- 查 dumpsys 的 Estimated power use 分项（屏幕/待机/硬件 mAh）
# 用法: su -c "sh /data/local/tmp/power-breakdown.sh"

D=/data/local/tmp/bs2.txt
dumpsys batterystats > $D 2>/dev/null
echo "行数: $(wc -l < $D)"
echo ""

echo "########## 1. Estimated power use 全段 ##########"
awk '/Estimated power use/,/^$/' $D | head -n 40 | sed 's/^/  /'

echo ""
echo "########## 2. Capacity / Computed drain / actual drain ##########"
grep -E 'Capacity:|Computed drain|actual drain' $D | head -n 3 | sed 's/^/  /'

echo ""
echo "########## 3. 顶层汇总（Screen/CPU/Radio/Wifi 的时长与 mAh） ##########"
grep -E '^ +(Screen|Phone|Wifi|Volume|Battery|Idle|Total):' $D | head -n 20 | sed 's/^/  /'

echo ""
echo "########## 4. time in state 段 ##########"
awk '/Time in state:/,/^$/' $D | head -n 20 | sed 's/^/  /'

echo ""
echo "########## 5. 屏幕亮屏总时长 ##########"
grep -iE 'screen.?time|Screen:' $D | head -n 8 | sed 's/^/  /'

echo ""
echo "########## 6. 每 UID 的 screen= 值（前 5） ##########"
grep -oE 'screen=[0-9.]+' $D | head -n 5 | sed 's/^/  /'

echo ""
echo "########## 7. 内核层：实际电流（若有） ##########"
for F in /sys/class/power_supply/battery/current_now \
         /sys/class/power_supply/battery/charge_now \
         /sys/class/power_supply/battery/capacity \
         /sys/class/power_supply/battery/voltage_now \
         /sys/class/power_supply/bms/op_volt \
         /sys/class/power_supply/bms/iavg \
         /sys/class/power_supply/bms/charge_full; do
  [ -r "$F" ] && echo "  $F = $(cat $F 2>/dev/null)"
done
