#!/system/bin/sh
# verify_pl.sh -- 验证 PowerLens 1.1 的修复效果
# 用法: su -c "sh /data/local/tmp/verify_pl.sh"

APP=/data/data/com.powerlens.app
PKG=com.powerlens.app

echo "########## 1. 恢复历史数据 ##########"
UID_APP=$(dumpsys package $PKG | grep -m1 'userId=' | tr -d ' ' | cut -d= -f2)
echo "  app uid=$UID_APP"
cp -f /data/local/tmp/plc/keep.tsv "$APP/files/snapshots.tsv" 2>/dev/null
# SELinux 上下文必须跟目录一致，否则 app 读不到
DIRCTX=$(ls -Zd "$APP/files" 2>/dev/null | awk '{print $1}')
chown u0_a427:u0_a427 "$APP/files/snapshots.tsv" 2>/dev/null
chmod 644 "$APP/files/snapshots.tsv"
if [ -n "$DIRCTX" ]; then
  chcon "$DIRCTX" "$APP/files/snapshots.tsv" 2>/dev/null
fi
ls -lZ "$APP/files/snapshots.tsv"

echo
echo "########## 2. 启动并触发采集 ##########"
am force-stop $PKG
sleep 1
am start -W -n $PKG/.MainActivity 2>&1 | grep -E 'Status|TotalTime'

echo
echo "########## 3. 等采集完成（12 s）##########"
sleep 12
echo "--- status.txt 最近 4 条 ---"
tail -n 4 "$APP/files/status.txt" 2>/dev/null | sed 's/^/  /'

echo
echo "########## 4. 新写入的 battery 行（应有 status 列）##########"
tail -n 1 "$APP/files/snapshots.tsv" | sed 's/^/  /'
echo "  --- 第 6 列取值分布（全部 battery 行）---"
awk -F'\t' '$2=="*"{print $6}' "$APP/files/snapshots.tsv" | sort | uniq -c | sed 's/^/    /'

echo
echo "########## 5. 用 C 版核算（同一套算法）##########"
/data/local/tmp/plc/plcollect --status -f "$APP/files/snapshots.tsv"

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
echo "########## 7. logcat 里的 PowerLens ##########"
logcat -d -s PowerLens:* 2>/dev/null | tail -n 12 | sed 's/^/  /'