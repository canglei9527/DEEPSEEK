#!/system/bin/sh
# final_check.sh -- PowerLens 1.1 最终验证
# 用法: su -c "sh /data/local/tmp/final_check.sh"

APP=/data/data/com.powerlens.app
PKG=com.powerlens.app
F=$APP/files/snapshots.tsv

echo "########## 1. 清掉旧采集器，避免重复写 ##########"
# 停掉旧的 daemon.sh（每 600s 用 dumpsys 采集，已被 C 版取代）
for P in $(ps -A -o PID,ARGS 2>/dev/null | grep -E 'daemon\.sh|pass\.sh' | grep -v grep | awk '{print $1}'); do
  kill -9 "$P" 2>/dev/null && echo "  killed old collector pid=$P"
done
# 禁用开机启动的旧采集器
if [ -f /data/adb/service.d/30-powerlens.sh ]; then
  mv /data/adb/service.d/30-powerlens.sh /data/adb/service.d/30-powerlens.sh.disabled
  echo "  已禁用 30-powerlens.sh（旧 dumpsys 采集器）"
fi

echo
echo "########## 2. 启动新版 ##########"
am force-stop $PKG
sleep 1
am start -W -n $PKG/.MainActivity 2>&1 | grep -E 'Status|TotalTime'

echo
echo "########## 3. 等 15 s 让首次采集完成 ##########"
sleep 15
echo "--- status.txt ---"
tail -n 5 "$APP/files/status.txt" 2>/dev/null | sed 's/^/  /'

echo
echo "########## 4. 数据文件 ##########"
echo "  battery 行数: $(grep -c battery $F 2>/dev/null)"
echo "  最后 3 条 battery（第4列=电量计，第6列=状态）:"
grep battery "$F" 2>/dev/null | tail -n 3 | sed 's/^/    /'

echo
echo "  --- 新增的 battery 行里 status 列是否非 0 ---"
awk -F'\t' '$2=="*" && $6!="0"{print "    OK: " $0}' "$F" 2>/dev/null | tail -n 3
echo "  非0状态行数: $(awk -F'\t' '$2=="*" && $6!="0"' "$F" 2>/dev/null | wc -l)"

echo
echo "########## 5. 核心核算（C 版，与 Java 同算法）##########"
/data/local/tmp/plc/plcollect --status -f "$F"

echo
echo "########## 6. 进程开销 ##########"
PID=$(pidof $PKG 2>/dev/null)
if [ -n "$PID" ]; then
  grep -E 'VmRSS|VmSwap|Threads' /proc/$PID/status | sed 's/^/  /'
  S1=$(awk '{print $14+$15}' /proc/$PID/stat)
  sleep 5
  S2=$(awk '{print $14+$15}' /proc/$PID/stat)
  echo "  5s CPU tick: $((S2-S1)) (= $(( (S2-S1)*10 )) ms)"
fi

echo
echo "########## 7. JobScheduler ##########"
dumpsys jobscheduler 2>/dev/null | grep -A6 '#u0a428/4711' | grep -E 'PERIODIC|Service' | sed 's/^/  /'

echo
echo "########## 8. logcat ##########"
logcat -d -s PowerLens:* 2>/dev/null | tail -n 8 | sed 's/^/  /'