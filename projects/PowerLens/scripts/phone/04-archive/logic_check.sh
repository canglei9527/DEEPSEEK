#!/system/bin/sh
# logic_check.sh -- 用 App 自己的代码路径验证核算（不依赖截图）
# 用法: su -c "sh /data/local/tmp/logic_check.sh"

F=/data/data/com.powerlens.app/files/snapshots.tsv

echo "########## 1. 数据文件现状 ##########"
ls -l "$F"
echo "  总行数: $(wc -l < $F)"
echo "  battery 行数: $(grep -c battery $F)"
echo "  C 守护写的（第4列>10万 = µAh）: $(awk -F'\t' '$2=="*" && $4>100000' $F | wc -l)"
echo "  旧 Java 写的（第4列<10万 = mAh）: $(awk -F'\t' '$2=="*" && $4>0 && $4<100000' $F | wc -l)"

echo
echo "########## 2. 两种算法对比 ##########"
echo "--- C 版（--status，正确实现） ---"
/data/local/tmp/plc/plcollect --status -f "$F"

echo
echo "########## 3. 旧版算法（错的，用来对照） ##########"
awk -F'\t' '
  $2=="*" {
    v = $4
    if (v > 100000) v = v / 1000     # µAh -> mAh
    if (first == 0) { first = v; first_ts = $1 }
    last = v; last_ts = $1; n++
  }
  END {
    printf "  采样点: %d\n", n
    printf "  首值: %.0f mAh  末值: %.0f mAh\n", first, last
    printf "  旧算法（首-末）= %.0f mAh  <-- 这是 1.0 显示的数字\n", first - last
    if (last_ts > first_ts) {
      h = (last_ts - first_ts) / 3600000.0
      printf "  跨度: %.2f h，按旧算法折算 %.0f mA\n", h, (first-last)/h
    }
  }' "$F"

echo
echo "########## 4. App 侧读取验证（触发 refresh 并看 logcat） ##########"
logcat -c 2>/dev/null
am force-stop com.powerlens.app
sleep 1
am start -n com.powerlens.app/.MainActivity > /dev/null 2>&1
sleep 6
echo "--- PowerLens 日志 ---"
logcat -d -s PowerLens:* 2>/dev/null | grep -vE 'JobScheduler|RemoteException|android\.|java\.|at ' | tail -n 10

echo
echo "########## 5. 进程开销（关键指标） ##########"
PID=$(pidof com.powerlens.app 2>/dev/null)
if [ -n "$PID" ]; then
  grep -E 'VmRSS|VmSwap' /proc/$PID/status | sed 's/^/  /'
  S1=$(awk '{print $14+$15}' /proc/$PID/stat)
  sleep 10
  S2=$(awk '{print $14+$15}' /proc/$PID/stat)
  echo "  10s CPU: $(( (S2-S1)*10 )) ms  => $(( (S2-S1)*100/100 ))‰ 单核"
fi

echo
echo "########## 6. C 守护状态 ##########"
ps -A -o PID,ARGS 2>/dev/null | grep plc-daemon | grep -v grep | sed 's/^/  /'
tail -n 3 /data/local/tmp/plc/daemon.log 2>/dev/null | sed 's/^/  /'
echo "  下次采集间隔: $(grep -c . /data/local/tmp/plc/daemon.log 2>/dev/null) 条日志"