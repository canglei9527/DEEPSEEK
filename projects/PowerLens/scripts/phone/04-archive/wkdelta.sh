#!/system/bin/sh
# wkdelta.sh -- 60 秒内各唤醒源的增量 + netfilter 规则现状
# 用法: su -c "sh /data/local/tmp/wkdelta.sh"

W=/sys/kernel/debug/wakeup_sources
echo "== 采集 t0 =="
awk 'NR>1 && NF>=8 {print $1, $2, $3}' "$W" 2>/dev/null | sort > /data/local/tmp/_w0.txt
RX0=$(cat /sys/class/net/wlan0/statistics/rx_packets)
E0=$(dmesg 2>/dev/null | grep -c 'PM: suspend entry')
A0=$(dmesg 2>/dev/null | grep -c 'Abort: Pending Wakeup Sources')
S0=$(cat /sys/power/suspend_stats/success)
F0=$(cat /sys/power/suspend_stats/fail)
T0=$(date +%s)

sleep 60

echo "== 采集 t1 =="
awk 'NR>1 && NF>=8 {print $1, $2, $3}' "$W" 2>/dev/null | sort > /data/local/tmp/_w1.txt
RX1=$(cat /sys/class/net/wlan0/statistics/rx_packets)
E1=$(dmesg 2>/dev/null | grep -c 'PM: suspend entry')
A1=$(dmesg 2>/dev/null | grep -c 'Abort: Pending Wakeup Sources')
S1=$(cat /sys/power/suspend_stats/success)
F1=$(cat /sys/power/suspend_stats/fail)
T1=$(date +%s)
DT=$((T1 - T0)); [ $DT -le 0 ] && DT=60

echo "===== 60 s 增量（DT=${DT}s）====="
echo "  wlan0 rx_packets: +$((RX1 - RX0))  ($(( (RX1 - RX0) / DT ))/s)"
echo "  suspend entry:    +$((E1 - E0))"
echo "  abort:            +$((A1 - A0))"
echo "  suspend success:  +$((S1 - S0))"
echo "  suspend fail:     +$((F1 - F0))"

echo
echo "===== 唤醒源 active_count 增量 top 15 ====="
join /data/local/tmp/_w0.txt /data/local/tmp/_w1.txt 2>/dev/null | awk '
{ d = $4 - $2; e = $5 - $3; if (d != 0 || e != 0) printf "  %-34s active+%-8s event+%s\n", $1, d, e }
' | sort -k2 -t+ -nr | head -n 15

echo
echo "===== netfilter 现状（mcast 屏蔽是否真生效）====="
echo '--- iptables filter INPUT ---'
iptables -L INPUT -n -v 2>/dev/null | head -n 25
echo '--- ip6tables filter INPUT ---'
ip6tables -L INPUT -n -v 2>/dev/null | head -n 25

echo
echo "===== 10-mcast.sh 内容 ====="
cat /data/adb/service.d/10-mcast.sh 2>/dev/null