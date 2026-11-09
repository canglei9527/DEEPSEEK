#!/system/bin/sh
# qrtr2.sh -- 精简版：只查 QRTR 高频的根因，不做全 fd 扫描
# 用法: su -c "sh /data/local/tmp/qrtr2.sh"

W=/sys/kernel/debug/wakeup_sources

echo "########## 1. qrtr_ws 10 秒增量 ##########"
q0=$(grep '^qrtr_ws' $W 2>/dev/null | awk '{print $2, $3}')
sleep 10
q1=$(grep '^qrtr_ws' $W 2>/dev/null | awk '{print $2, $3}')
echo "$q0 $q1" | awk 'NF==4{printf "  active +%d  event +%d  => %.0f 次/秒\n", $3-$1, $4-$2, ($4-$2)/10}'

echo
echo "########## 2. 所有 eventpoll 实例 ##########"
grep '^eventpoll' $W 2>/dev/null | sort -k3 -nr | awk '{printf "  active=%-10s event=%-10s wake=%-8s total=%s\n", $2,$3,$4,$7}'

echo
echo "########## 3. 全部 timerfd 实例 ##########"
grep '^\[timerfd\]' $W 2>/dev/null | sort -k3 -nr | awk '{printf "  active=%-10s event=%-10s wake=%-8s\n", $2,$3,$4}'

echo
echo "########## 4. gh_* / hypervisor ##########"
grep -E '^gh_|^trustedvm|^fastrpc|^14343' $W 2>/dev/null | sort -k3 -nr | head -n 12 | awk '{printf "  %-24s active=%-9s event=%-9s\n", $1,$2,$3}'

echo
echo "########## 5. QRTR 是哪个模块 ##########"
ls /sys/module 2>/dev/null | grep -iE 'qrtr' | sed 's/^/  /'
echo "  --- 内核线程 ---"
ps -A -o PID,NAME 2>/dev/null | grep -i qrtr | sed 's/^/  /'
echo "  --- /dev/qrtr* ---"
ls -l /dev/qrtr* /dev/socket/qrtr* 2>/dev/null | sed 's/^/  /'

echo
echo "########## 6. 只看 system_server + 主应用的 epoll 数（不做全扫） ##########"
for PKG in system_server com.tencent.mm com.eg.android.AlipayGphone tv.danmaku.bili com.miui.powerkeeper com.android.phone; do
  PID=$(pidof $PKG 2>/dev/null | awk '{print $1}')
  [ -z "$PID" ] && continue
  n=$(grep -lE 'events: [2-9a-fA-F][0-9a-fA-F]{7}' /proc/$PID/fdinfo/* 2>/dev/null | wc -l)
  printf '  %-32s pid=%-7s EPOLLWAKEUP fd=%s\n' "$PKG" "$PID" "$n"
done

echo
echo "########## 7. rmnet 10 秒收包 ##########"
for n in rmnet_data1 rmnet_data4 rmnet_ipa0; do
  [ -r /sys/class/net/$n/statistics/rx_packets ] && echo "$n $(cat /sys/class/net/$n/statistics/rx_packets)"
done > /tmp/rm0
sleep 10
for n in rmnet_data1 rmnet_data4 rmnet_ipa0; do
  [ -r /sys/class/net/$n/statistics/rx_packets ] && echo "$n $(cat /sys/class/net/$n/statistics/rx_packets)"
done > /tmp/rm1
join /tmp/rm0 /tmp/rm1 2>/dev/null | awk '{printf "  %-14s +%d 包/10s  (%.1f/秒)\n", $1, $3-$2, ($3-$2)/10}'

echo
echo "########## 8. 移动数据开关 ##########"
echo "  mobile_data=$(settings get global mobile_data 2>/dev/null)  airplane=$(settings get global airplane_mode_on 2>/dev/null)"
echo "  --- 活跃 SIM 数据状态 ---"
dumpsys telephony.registry 2>/dev/null | grep -oE 'mDataConnectionState=[0-9]+' | head -n 4 | sed 's/^/  /'