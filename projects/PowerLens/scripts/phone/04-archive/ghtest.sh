#!/system/bin/sh
# ghtest.sh -- 验证 gh_vcpu_ws（gunyah hypervisor vCPU）是不是 116 mA 的主因
#
# 已知：
#   gh_vcpu_ws_45_0  event=462431   (累计)
#   gh_vcpu_ws_45_1  event=402041
#   gh_vcpu_ws_49_0  event=20764
#   gh_vcpu_ws_49_1  event=18691
#   旧报告的 460ms 固定唤醒链: kworker[timerfd] -> dp_rx_thread_0 -> tee-supplicant[gh_vcpu_ws] -> AlarmManager -> trustedvm_vcpu0/1
#
# 想测的：
#   1) 45_* 和 49_* 分别属于哪个 VM
#   2) 关掉 TEE/trustedvm 相关服务后，唤醒频率是否下降
#
# 注意：不能真的停掉 TEE（会导致指纹/支付失效），
#       所以先只做"观测"，找可安全降频的开关。
#
# 用法: su -c "sh /data/local/tmp/ghtest.sh"

W=/sys/kernel/debug/wakeup_sources

echo "########## 1. gunyah 虚拟机清单 ##########"
ls -l /sys/kernel/gunyah 2>/dev/null | sed 's/^/  /'
for d in /sys/kernel/gunyah/*/; do
  [ -d "$d" ] || continue
  echo "  --- $d ---"
  for f in name vmid cpus; do
    [ -r "$d$f" ] && printf '    %-8s %s\n' "$f" "$(cat $d$f 2>/dev/null | tr '\n' ' ')"
  done
done 2>/dev/null | head -n 40

echo
echo "  --- 从 dmesg 找 gunyah VM 信息 ---"
dmesg 2>/dev/null | grep -iE 'gunyah|gh_vcpu|trustedvm|qseecom' | tail -n 12 | sed 's/^/    /'

echo
echo "########## 2. gh_vcpu_ws 归属哪两个 VM ##########"
echo "  45_* 与 49_* 的 event 差值（60 秒）"
for n in gh_vcpu_ws_45_0 gh_vcpu_ws_45_1 gh_vcpu_ws_49_0 gh_vcpu_ws_49_1; do
  echo "$n $(grep "^$n" $W 2>/dev/null | awk '{print $2, $3}')"
done > /tmp/gh0
sleep 60
for n in gh_vcpu_ws_45_0 gh_vcpu_ws_45_1 gh_vcpu_ws_49_0 gh_vcpu_ws_49_1; do
  echo "$n $(grep "^$n" $W 2>/dev/null | awk '{print $2, $3}')"
done > /tmp/gh1
join /tmp/gh0 /tmp/gh1 2>/dev/null | awk '{
  printf "  %-20s active +%-8d event +%-8d => %.1f 次/秒\n", $1, $3-$2, $5-$4, ($5-$4)/60
}'

echo
echo "########## 3. 谁在唤起 gh_vcpu（TEE 相关进程） ##########"
echo "  --- tee / keymaster / gatekeeper ---"
ps -A -o PID,NAME 2>/dev/null | grep -iE 'tee|keymaster|gatekeeper|trustedvm|qsee' | grep -v grep | sed 's/^/    /'
echo "  --- 这些进程的 CPU 时间 ---"
for nm in tee-supplicant vendor.qti.hardware.tui_comm android.hardware.gatekeeper android.hardware.keymaster; do
  PID=$(pidof $nm 2>/dev/null | awk '{print $1}')
  [ -z "$PID" ] && continue
  t=$(awk '{print $14+$15}' /proc/$PID/stat 2>/dev/null)
  printf '    %-46s pid=%-7s cpu_ticks=%s\n' "$nm" "$PID" "$t"
done

echo
echo "########## 4. 60 秒内这些进程的 CPU 增量 ##########"
PIDS=""
for nm in tee-supplicant qrtr_ns; do
  P=$(pidof $nm 2>/dev/null | awk '{print $1}'); [ -n "$P" ] && PIDS="$PIDS $P"
done
echo "  监控 PIDs:$PIDS"
for p in $PIDS; do echo "$p $(awk '{print $14+$15}' /proc/$p/stat 2>/dev/null)"; done > /tmp/t0
sleep 60
for p in $PIDS; do echo "$p $(awk '{print $14+$15}' /proc/$p/stat 2>/dev/null)"; done > /tmp/t1
join /tmp/t0 /tmp/t1 2>/dev/null | while read pid a b; do
  nm=$(cat /proc/$pid/comm 2>/dev/null)
  echo "  $nm(pid=$pid): +$((b-a)) ticks = $(( (b-a)*10 )) ms/60s"
done

echo
echo "########## 5. 当前 TEE / trustedvm 的持有者（wakelock 侧） ##########"
dumpsys power 2>/dev/null | sed -n '/Wake Locks/,/^$/p' | head -n 10 | sed 's/^/  /'
echo "  --- /sys/power/wake_lock ---"
cat /sys/power/wake_lock 2>/dev/null | sed 's/^/    /'

echo
echo "########## 6. 是否有 fastrpc（ADSP/NPU）在活动 ##########"
grep -E '^fastrpc' $W 2>/dev/null | awk '{printf "  %-24s active=%-9s event=%-9s\n", $1,$2,$3}'
echo "  --- CDSP/ADSP 相关进程 ---"
ps -A -o PID,NAME 2>/dev/null | grep -iE 'adsprpc|cdsprpc|fastrpc|sdsprpcd' | sed 's/^/    /'

echo
echo "########## 7. 可安全调整的开关候选 ##########"
echo "  --- NFC（已知未关） ---"
svc nfc 2>/dev/null | sed 's/^/    /'
settings get secure nfc_on 2>/dev/null | sed 's/^/    nfc_on=/'
echo "  --- 蓝牙 ---"
settings get global bluetooth_on 2>/dev/null | sed 's/^/    bluetooth_on=/'
echo "  --- 定位 ---"
settings get secure location_mode 2>/dev/null | sed 's/^/    location_mode=/'
echo "  --- 传感器 ---"
dumpsys sensorservice 2>/dev/null | grep -cE 'active' | sed 's/^/    活跃传感器数: /'