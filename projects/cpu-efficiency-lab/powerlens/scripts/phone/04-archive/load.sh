#!/system/bin/sh
# load.sh -- 谁在吃 CPU / 谁在制造网络唤醒
# 用法: su -c "sh /data/local/tmp/load.sh"

echo "===== LOADAVG / CPU ====="
cat /proc/loadavg
echo -n 'online cpus: '; cat /sys/devices/system/cpu/online
echo

echo "===== TOP 15 (by cpu time delta) ====="
top -b -n 2 -d 1 -o %CPU -m 15 2>/dev/null | tail -n 20

echo
echo "===== 每个核的频率 ====="
for c in 0 1 2 3 4 5 6 7; do
    f=/sys/devices/system/cpu/cpu$c/cpufreq/scaling_cur_freq
    [ -r "$f" ] && printf 'cpu%d %s kHz\n' "$c" "$(cat $f)"
done

echo
echo "===== 网络接口 ====="
ip -o -4 addr show 2>/dev/null | awk '{print $2, $4}'
echo "--- 各接口收发计数 ---"
for i in /sys/class/net/*/statistics/rx_packets; do
    n=$(echo "$i" | cut -d/ -f5)
    printf '%-10s rx=%-12s tx=%-12s\n' "$n" "$(cat $i 2>/dev/null)" "$(cat /sys/class/net/$n/statistics/tx_packets 2>/dev/null)"
done

echo
echo "===== 移动数据状态 ====="
settings get global mobile_data 2>/dev/null | sed 's/^/mobile_data: /'
dumpsys telephony.registry 2>/dev/null | grep -E 'mDataConnectionState|mDataActivity|mServiceState' | head -n 5

echo
echo "===== 当前前台/常驻可疑进程 ====="
ps -A -o PID,PPID,RSS,NAME 2>/dev/null | grep -Ei 'kworker|irq|tee-supplicant|freezer|collector|powerlens|thermal' | head -n 25

echo
echo "===== 中断计数 top (1s delta) ====="
snap() { awk '/^ *[0-9]+:/{print $1, $2}' /proc/interrupts 2>/dev/null | tr -d ':' | awk '{s[$1]+=$2} END{for(k in s) print k, s[k]}'; }
snap > /data/local/tmp/irq_a.txt
sleep 2
snap > /data/local/tmp/irq_b.txt
join /data/local/tmp/irq_a.txt /data/local/tmp/irq_b.txt 2>/dev/null | awk '{d=$3-$2; if(d>50) printf "irq %-4s +%s/2s\n", $1, d}' | sort -k3 -nr | head -n 15
rm -f /data/local/tmp/irq_a.txt /data/local/tmp/irq_b.txt