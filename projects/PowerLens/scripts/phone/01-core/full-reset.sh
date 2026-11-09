#!/system/bin/sh
# full-reset.sh -- 一次性把 PowerLens 环境恢复到正确状态
#
# 做这几件事（幂等，可重复运行）：
#   1. 杀掉所有旧采集器（powerlens/daemon.sh、重复的 plc-daemon）
#   2. 禁用旧 service.d 脚本
#   3. 装 plc-daemon.sh v2（含权限自愈）
#   4. 启动唯一守护实例
#   5. 修正数据文件权限
#   6. 重启 App 并验证
#
# 用法: su -c "sh /data/local/tmp/full-reset.sh"

PKG=com.powerlens.app
D=/data/local/tmp/plc
F=/data/data/$PKG/files/snapshots.tsv
LOG=$D/daemon.log

echo "########## 1. 杀所有旧采集器 ##########"

# 1.1 旧架构：/data/local/tmp/powerlens/daemon.sh + pass.sh
for P in $(ps -A -o PID,ARGS 2>/dev/null | grep 'powerlens/daemon\.sh' | grep -v grep | awk '{print $1}'); do
  kill -9 "$P" 2>/dev/null && echo "  killed 旧 daemon pid=$P"
done
for P in $(ps -A -o PID,ARGS 2>/dev/null | grep 'powerlens/pass\.sh' | grep -v grep | awk '{print $1}'); do
  kill -9 "$P" 2>/dev/null && echo "  killed 旧 pass pid=$P"
done

# 1.2 所有 plc-daemon（含重复实例）
for P in $(ps -A -o PID,ARGS 2>/dev/null | grep 'plc-daemon\.sh' | grep -v grep | awk '{print $1}'); do
  kill -9 "$P" 2>/dev/null && echo "  killed plc-daemon pid=$P"
done
rm -f $D/daemon.pid
sleep 2

echo
echo "########## 2. 禁用旧 service.d ##########"
for f in /data/adb/service.d/30-powerlens.sh /data/adb/service.d/20-freezer.sh; do
  if [ -f "$f" ]; then
    mv "$f" "$f.disabled" 2>/dev/null && echo "  已禁用 $(basename $f)"
  fi
done
echo "  --- 当前 service.d ---"
ls -l /data/adb/service.d/ 2>/dev/null | sed 's/^/    /'

echo
echo "########## 3. 检查组件齐备 ##########"
for f in "$D/plcollect" "$D/plc-daemon.sh"; do
  if [ -x "$f" ]; then
    echo "  [OK] $f ($(wc -c < $f) B)"
  else
    echo "  [缺] $f"
  fi
done

echo
echo "########## 4. 启动唯一守护 ##########"
if [ -x "$D/plcollect" ] && [ -f "$D/plc-daemon.sh" ]; then
  setsid sh "$D/plc-daemon.sh" 600 >/dev/null 2>&1 &
  sleep 4
fi
N=$(ps -A -o ARGS 2>/dev/null | grep 'plc-daemon\.sh' | grep -v grep | wc -l)
echo "  实例数: $N"
ps -A -o PID,ARGS 2>/dev/null | grep 'plc-daemon\.sh' | grep -v grep | sed 's/^/    /'

echo
echo "########## 5. 修正数据权限 ##########"
APPUID=$(stat -c '%u' "/data/data/$PKG/files" 2>/dev/null)
echo "  App uid = $APPUID"
if [ -f "$F" ] && [ -n "$APPUID" ]; then
  chown "$APPUID:$APPUID" "$F" 2>/dev/null
  chmod 644 "$F" 2>/dev/null
  CTX=$(ls -Zd "$(dirname $F)" 2>/dev/null | awk '{print $1}')
  [ -n "$CTX" ] && chcon "$CTX" "$F" 2>/dev/null
  ls -lZ "$F" | sed 's/^/  /'
  if su "$APPUID" -c "head -c 10 '$F' >/dev/null 2>&1"; then
    echo "  [OK] App 可读"
  else
    echo "  [FAIL] App 仍读不到"
  fi
fi

echo
echo "########## 6. 数据格式健康度 ##########"
if [ -r "$F" ]; then
  echo "  总行数: $(wc -l < $F)"
  echo "  --- 电池行分类 ---"
  awk -F'\t' '$2=="*" {
    if ($4 > 100000)      print "    正常µAh"
    else if ($4 > 0)      print "    旧mAh"
    else                  print "    空行"
  }' "$F" | sort | uniq -c | sed 's/^/  /'
  echo "  --- 最近 2 条 ---"
  awk -F'\t' '$2=="*"' "$F" | tail -n 2 | sed 's/^/    /'
fi

echo
echo "########## 7. 重启 App ##########"
am force-stop $PKG
sleep 1
logcat -c 2>/dev/null
am start -W -n $PKG/.MainActivity 2>&1 | grep -E 'Status|TotalTime'
sleep 6

echo
echo "########## 8. App 日志（应无 EACCES / 写入失败）##########"
logcat -d -s PowerLens:* 2>/dev/null | tail -n 12 | sed 's/^/  /'

echo
echo "########## 9. 守护日志 ##########"
tail -n 8 "$LOG" 2>/dev/null | sed 's/^/  /'

echo
echo "########## 10. 最终进程清单 ##########"
echo "  --- 采集相关 ---"
ps -A -o PID,ARGS 2>/dev/null | grep -E 'plc-daemon|plcollect|powerlens/daemon' | grep -v grep | sed 's/^/    /'
echo "  --- App ---"
ps -A -o PID,ARGS 2>/dev/null | grep "$PKG" | grep -v grep | sed 's/^/    /'