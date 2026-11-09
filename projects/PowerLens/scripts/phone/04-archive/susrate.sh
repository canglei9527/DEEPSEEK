#!/system/bin/sh
# susrate.sh -- 30 秒内挂起尝试频率（判断 timerfd 的节律）
# 用法: su -c "sh /data/local/tmp/susrate.sh"

echo "== 清空 dmesg 缓冲 =="
dmesg -c > /dev/null 2>&1
T0=$(date +%s)

sleep 30

T1=$(date +%s)
DT=$((T1 - T0))
[ "$DT" -le 0 ] && DT=30

E=$(dmesg 2>/dev/null | grep -c 'PM: suspend entry')
A=$(dmesg 2>/dev/null | grep -c 'Abort: Pending Wakeup Sources')
X=$(dmesg 2>/dev/null | grep -c 'PM: suspend exit')
D=$(dmesg 2>/dev/null | grep -c 'PM: suspend entry (deep)')

echo "  窗口 ${DT}s: entry=$E exit=$X abort=$A deep=$D"
echo "  频率: $(awk "BEGIN{printf \"%.2f\", $E/$DT}") 次/秒"
echo "  平均间隔: $(awk "BEGIN{if($E>0) printf \"%.0f\", $DT*1000/$E; else print \"n/a\"}") ms"

echo
echo "== 中止来源 =="
dmesg 2>/dev/null | grep 'Abort: Pending Wakeup Sources' \
  | sed 's/.*Pending Wakeup Sources: //' | tr ' ' '\n' | grep -v '^$' \
  | sort | uniq -c | sort -nr | head -n 10

echo
echo "== 最近 6 条原始记录 =="
dmesg 2>/dev/null | grep -E 'PM: suspend|Abort: Pending' | tail -n 6

echo
echo "== 同时段电量计 =="
echo "  charge_counter = $(cat /sys/class/power_supply/battery/charge_counter) uAh"
echo "  current_now    = $(cat /sys/class/power_supply/battery/current_now) uA"
echo "  status         = $(cat /sys/class/power_supply/battery/status)"