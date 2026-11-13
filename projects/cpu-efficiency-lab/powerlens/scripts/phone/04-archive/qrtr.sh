#!/system/bin/sh
# qrtr.sh -- 追查 qrtr_ws（1579 次/秒）+ eventpoll（2422 次/秒）
#
# qrtr_ws = Qualcomm IPC Router wakeup source
#   用于 AP <-> modem / ADSP / CDSP / NPU 之间的 socket 通信
#   每秒 1579 次说明有进程在疯狂轮询 QRTR
#
# eventpoll = EPOLLWAKEUP，任何 epoll 注册了该标志的 fd
#   每秒 2422 次，需要找出是谁
#
# 用法: su -c "sh /data/local/tmp/qrtr.sh"

echo "########## 1. 谁在用 QRTR ##########"
echo "--- /dev/socket 里的 qrtr 相关 ---"
ls -l /dev/socket 2>/dev/null | grep -iE 'qrtr|ipcr|rmnet' | sed 's/^/  /'
echo "--- 打开 qrtr 设备的进程 ---"
for p in /proc/[0-9]*; do
  pid=${p#/proc/}
  for fd in $p/fd/*; do
    t=$(readlink "$fd" 2>/dev/null)
    case "$t" in
      *qrtr*|*ipcr*)
        nm=$(tr -d '\000' < $p/cmdline 2>/dev/null | head -c 50)
        [ -z "$nm" ] && nm=$(cat $p/comm 2>/dev/null)
        echo "  pid=$pid $t -> $nm"
        ;;
    esac
  done
done 2>/dev/null | sort -u | head -n 20

echo
echo "########## 2. QRTR 内核节点 ##########"
ls -l /sys/kernel/debug/qrtr 2>/dev/null | sed 's/^/  /'
[ -r /sys/kernel/debug/qrtr/stats ] && cat /sys/kernel/debug/qrtr/stats | sed 's/^/  /'
find /sys/module -maxdepth 1 -name '*qrtr*' 2>/dev/null | sed 's/^/  module: /'

echo
echo "########## 3. QRTR 每 10 秒增量（确认高频） ##########"
W=/sys/kernel/debug/wakeup_sources
q0=$(grep '^qrtr_ws' $W 2>/dev/null | awk '{print $2, $3}')
sleep 10
q1=$(grep '^qrtr_ws' $W 2>/dev/null | awk '{print $2, $3}')
echo "$q0 $q1" | awk '{printf "  qrtr_ws: active +%d  event +%d  => %.0f 次/秒\n", $3-$1, $4-$2, ($4-$2)/10}'

echo
echo "########## 4. 所有 eventpoll 实例及其归属 ##########"
echo "--- wakeup_sources 里的 eventpoll（按 event 数排序） ---"
grep '^eventpoll' $W 2>/dev/null | sort -k3 -nr | sed 's/^/  /'

echo
echo "########## 5. 找带 EPOLLWAKEUP 且 fd 数最多的进程 ##########"
# EPOLLWAKEUP = 0x20000000，fdinfo 里 events 字段是 hex
for d in /proc/[0-9]*; do
  pid=${d#/proc/}
  n=$(grep -lE 'events: [2-9a-fA-F][0-9a-fA-F]{7}' $d/fdinfo/* 2>/dev/null | wc -l)
  [ "$n" -gt 2 ] && {
    nm=$(tr -d '\000' < $d/cmdline 2>/dev/null | head -c 44)
    [ -z "$nm" ] && nm=$(cat $d/comm 2>/dev/null)
    echo "$n $pid $nm"
  }
done 2>/dev/null | sort -nr | head -n 15 | awk '{printf "  %2s fd  pid=%-7s %s\n", $1, $2, substr($0, index($0,$3))}'

echo
echo "########## 6. 是否 hypervisor / trustedvm 在活动 ##########"
echo "--- gh_* 唤醒源（gunyah hypervisor vCPU） ---"
grep -E '^gh_|^trustedvm|^vcpu' $W 2>/dev/null | sed 's/^/  /'
echo "--- gunyah 模块 ---"
ls /sys/module 2>/dev/null | grep -iE 'gunyah|gh_' | sed 's/^/  /'
echo "--- trustedvm 相关进程 ---"
ps -A -o PID,ARGS 2>/dev/null | grep -iE 'trustedvm|gunyah|gh_' | grep -v grep | sed 's/^/  /'

echo
echo "########## 7. modem 是否在活动（实时） ##########"
echo "--- 调制解调器日志计数 ---"
dmesg 2>/dev/null | grep -ciE 'mhi|rmnet|qmi' | sed 's/^/  dmesg 提及次数: /'
echo "--- QMI 相关 socket ---"
ls -l /dev/socket 2>/dev/null | grep -iE 'qmi|rmnet' | head -n 8 | sed 's/^/  /'
echo "--- IPA 唤醒源（IP 加速器） ---"
grep -E '^IPA|^rmnet|^rmnet_ctl' $W 2>/dev/null | sed 's/^/  /'

echo
echo "########## 8. 移动数据关闭了吗 ##########"
echo "  mobile_data=$(settings get global mobile_data 2>/dev/null)"
echo "  airplane=$(settings get global airplane_mode_on 2>/dev/null)"
echo "  --- rmnet 接口状态 ---"
ip -o link show 2>/dev/null | grep rmnet | awk '{print "  " $2, $9}' | head -n 8
echo "  --- 各 rmnet 10 秒收包 ---"
for n in rmnet_data0 rmnet_data1 rmnet_data4 rmnet_ipa0; do
  [ -r /sys/class/net/$n/statistics/rx_packets ] && echo "  $n = $(cat /sys/class/net/$n/statistics/rx_packets)"
done > /tmp/r0
sleep 10
for n in rmnet_data0 rmnet_data1 rmnet_data4 rmnet_ipa0; do
  [ -r /sys/class/net/$n/statistics/rx_packets ] && echo "  $n = $(cat /sys/class/net/$n/statistics/rx_packets)"
done > /tmp/r1
paste /tmp/r0 /tmp/r1 2>/dev/null | awk '{printf "  %-14s 10s 内 +%d 包\n", $1, $4-$2}'