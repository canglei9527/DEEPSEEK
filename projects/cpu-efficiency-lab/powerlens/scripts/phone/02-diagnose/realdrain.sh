#!/system/bin/sh
# realdrain.sh -- 息屏待机下，究竟谁在耗电（一次性汇总，纯客观数据）
#
# 前面已经排除的（有数据支撑）：
#   ✗ I/O：PSI io = 0.00%，iowait ≈ 0（之前的 86% 是 awk 字段错位）
#   ✗ CPU 空转：idle 80~95%
#   ✗ 应用层 CPU：top UID 合计 91 秒/96 秒，其中 dynsystem 40 秒是历史累计
#   ✗ PowerLens 自身：0 ms/10s
#
# 剩下的真实嫌疑（本脚本逐项量化）：
#   1) 挂起从未成功 → AP 一直醒着，这是上层一切耗电的前提
#   2) trustedvm / hypervisor 的 gh_vcpu_ws 唤醒源
#   3) 调制解调器（rmnet / IPA 唤醒源）
#   4) Wi-Fi（qcom_rx_wakelock）
#
# 用法: su -c "sh /data/local/tmp/realdrain.sh"

echo "########## 1. 挂起到底成不成功 ##########"
S0=$(cat /sys/power/suspend_stats/success 2>/dev/null)
F0=$(cat /sys/power/suspend_stats/fail 2>/dev/null)
E0=$(dmesg 2>/dev/null | grep -c 'PM: suspend entry')
X0=$(dmesg 2>/dev/null | grep -c 'PM: suspend exit')
R0=$(cat /sys/power/suspend_stats/total_hw_sleep 2>/dev/null)
T0=$(date +%s); C0=$(cat /sys/class/power_supply/battery/charge_counter)

sleep 60

S1=$(cat /sys/power/suspend_stats/success 2>/dev/null)
F1=$(cat /sys/power/suspend_stats/fail 2>/dev/null)
E1=$(dmesg 2>/dev/null | grep -c 'PM: suspend entry')
X1=$(dmesg 2>/dev/null | grep -c 'PM: suspend exit')
T1=$(date +%s); C1=$(cat /sys/class/power_supply/battery/charge_counter)
DT=$((T1-T0)); [ $DT -lt 1 ] && DT=60

echo "  ${DT}s 内:"
echo "    suspend entry  +$((E1-E0))   exit +$((X1-X0))"
echo "    suspend_stats.success +$((S1-S0))   fail +$((F1-F0))"
echo "    last_failed_dev = $(cat /sys/power/suspend_stats/last_failed_dev 2>/dev/null)"
echo "    last_failed_errno = $(cat /sys/power/suspend_stats/last_failed_errno 2>/dev/null)"
awk -v a=$C0 -v b=$C1 -v t=$DT 'BEGIN{printf "    电流: %.1f mA\n", (a-b)/1000*3600/t}'

echo
echo "########## 2. 唤醒源 60 秒增量 ##########"
W=/sys/kernel/debug/wakeup_sources
[ -r "$W" ] || mount -t debugfs none /sys/kernel/debug 2>/dev/null
if [ -r "$W" ]; then
  awk 'NR>1 && NF>=8 {print $1"|"$2"|"$3}' $W | sort > /tmp/w0
  sleep 60
  awk 'NR>1 && NF>=8 {print $1"|"$2"|"$3}' $W | sort > /tmp/w1
  awk -F'|' '
    FNR==NR { a[$1]=$2; e[$1]=$3; next }
    {
      da = $2 - a[$1]; de = $3 - e[$1]
      if (de > 0) printf "%d\t%s\t%d\t%d\n", de, $1, da, de
    }' /tmp/w0 /tmp/w1 | sort -nr | head -n 18 | \
  while IFS="$(printf '\t')" read de name da de2; do
    printf '  %-34s active+%-8s event+%s\n' "$name" "$da" "$de2"
  done
else
  echo "  wakeup_sources 不可读"
fi

echo
echo "########## 3. 调制解调器是否在活动 ##########"
echo "  移动数据: $(settings get global mobile_data 2>/dev/null)"
echo "  飞行模式: $(settings get global airplane_mode_on 2>/dev/null)"
for i in /sys/class/net/rmnet_data1/statistics/rx_packets /sys/class/net/rmnet_data4/statistics/rx_packets; do
  [ -r "$i" ] && echo "  $i = $(cat $i)"
done
echo "  --- 60 秒 rmnet 增量 ---"
a=$(cat /sys/class/net/rmnet_data1/statistics/rx_packets 2>/dev/null || echo 0)
b=$(cat /sys/class/net/rmnet_data1/statistics/tx_packets 2>/dev/null || echo 0)
sleep 60
c=$(cat /sys/class/net/rmnet_data1/statistics/rx_packets 2>/dev/null || echo 0)
d=$(cat /sys/class/net/rmnet_data1/statistics/tx_packets 2>/dev/null || echo 0)
echo "    rmnet_data1 rx +$((c-a)) tx +$((d-b))"

echo
echo "########## 4. Wi-Fi 60 秒增量 ##########"
a=$(cat /sys/class/net/wlan0/statistics/rx_packets 2>/dev/null || echo 0)
b=$(cat /sys/class/net/wlan0/statistics/tx_packets 2>/dev/null || echo 0)
sleep 60
c=$(cat /sys/class/net/wlan0/statistics/rx_packets 2>/dev/null || echo 0)
d=$(cat /sys/class/net/wlan0/statistics/tx_packets 2>/dev/null || echo 0)
echo "    wlan0 rx +$((c-a)) tx +$((d-b))  ($(( (c-a)/1 )) 包/分)"
echo "  Wi-Fi 节能: $(dumpsys wifi 2>/dev/null | grep -iE 'power save|mPowerSave' | head -n 2)"

echo
echo "########## 5. 当前电源状态 ##########"
for f in status capacity charge_counter current_now voltage_now temp; do
  printf '  %-16s %s\n' "$f" "$(cat /sys/class/power_supply/battery/$f 2>/dev/null)"
done
echo "  屏幕: $(dumpsys power 2>/dev/null | grep -o 'mWakefulness=[A-Za-z]*')"
echo "  背光: $(cat /sys/class/backlight/panel0-backlight/brightness 2>/dev/null)"
echo "  /sys/power/mem_sleep: $(cat /sys/power/mem_sleep 2>/dev/null)"
echo "  /sys/power/wake_lock: $(cat /sys/power/wake_lock 2>/dev/null | tr '\n' ' ')"

echo
echo "########## 6. 谁持有 wakelock（内核侧） ##########"
dumpsys power 2>/dev/null | sed -n '/Wake Locks/,/^$/p' | head -n 12 | sed 's/^/  /'