#!/system/bin/sh
# install_pl.sh -- 装 PowerLens 1.1 并验证
# 用法: su -c "sh /data/local/tmp/install_pl.sh"

APK=/data/local/tmp/plc/PowerLens.apk

echo "== 备份现有数据 =="
cp -f /data/data/com.powerlens.app/files/snapshots.tsv \
      /data/local/tmp/plc/snapshots-backup-$(date +%Y%m%d-%H%M%S).tsv 2>/dev/null
ls -l /data/local/tmp/plc/*.tsv 2>/dev/null | tail -n 3

echo
echo "== 安装（保留数据，-r 覆盖）=="
pm install -r "$APK" 2>&1 | tail -n 3

echo
echo "== 版本确认 =="
dumpsys package com.powerlens.app | grep -E 'versionName|versionCode|lastUpdateTime'

echo
echo "== 冷启动并测采集耗时 =="
am force-stop com.powerlens.app
sleep 1
am start -W -n com.powerlens.app/.MainActivity 2>&1 | grep -E 'TotalTime|Status'

echo
echo "== 触发一次采集（等 8 秒）=="
sleep 8
tail -n 6 /data/data/com.powerlens.app/files/status.txt 2>/dev/null

echo
echo "== 进程内存 =="
PID=$(pidof com.powerlens.app 2>/dev/null)
if [ -n "$PID" ]; then
  grep -E 'VmRSS|VmSwap|Threads' /proc/$PID/status | sed 's/^/  /'
else
  echo "  进程未运行"
fi

echo
echo "== 数据文件 =="
ls -l /data/data/com.powerlens.app/files/snapshots.tsv
echo "  battery 行数: $(grep -c battery /data/data/com.powerlens.app/files/snapshots.tsv)"
echo "  最后 2 条 battery:"
grep battery /data/data/com.powerlens.app/files/snapshots.tsv | tail -n 2 | sed 's/^/    /'