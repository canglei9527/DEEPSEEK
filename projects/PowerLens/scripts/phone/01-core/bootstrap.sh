#!/system/bin/sh
# bootstrap.sh -- 完全自举：清空 → 采 3 次 → 修权限 → 起守护
# 全部在手机端完成，不依赖 adb 持续连接
# 用法: su -c "setsid sh /data/local/tmp/bootstrap.sh >/dev/null 2>&1 &"

PKG=com.powerlens.app
D=/data/local/tmp/plc
F=/data/local/tmp/powerlens/snapshots.tsv
LOG=$D/bootstrap.log

: > $LOG
echo "===== bootstrap $(date '+%F %T') =====" >> $LOG

# 1. 停守护（避免干扰）
for P in $(ps -A -o PID,ARGS 2>/dev/null | grep 'plc-daemon.sh' | grep -v grep | awk '{print $1}'); do
  kill -9 "$P" 2>/dev/null
done
rm -f "$D/daemon.pid"
echo "守护已停" >> $LOG

# 2. 归档并清空
if [ -f "$F" ]; then
  cp -f "$F" "$D/snap-$(date +%Y%m%d-%H%M%S).tsv"
  echo "已归档 (行数 $(wc -l < $F))" >> $LOG
fi
: > "$F"

# 3. 采 3 次，间隔 30s
i=1
while [ $i -le 3 ]; do
  echo "--- 采集 $i/3 ---" >> $LOG
  "$D/plcollect" --once -f "$F" >> $LOG 2>&1
  [ $i -lt 3 ] && sleep 30
  i=$((i+1))
done

# 4. 修权限
mkdir -p /data/local/tmp/powerlens 2>/dev/null
chmod 777 /data/local/tmp/powerlens 2>/dev/null
chmod 666 "$F" 2>/dev/null
echo "权限已设 (tmp 路径 666)" >> $LOG

# 5. 起守护
setsid sh "$D/plc-daemon.sh" 600 >/dev/null 2>&1 &
sleep 2
echo "守护已启" >> $LOG
ps -A -o PID,ARGS 2>/dev/null | grep 'plc-daemon.sh' | grep -v grep >> $LOG

# 6. 验证
echo "" >> $LOG
echo "===== 结果 =====" >> $LOG
echo "行数: $(wc -l < $F)" >> $LOG
echo "--- 电池行 ---" >> $LOG
awk -F'\t' '$2=="*"{printf "  ts=%s charge=%s cap=%s status=%s\n", substr($1,6), $4, $5, $6}' "$F" >> $LOG
echo "--- 前 10 个 UID ---" >> $LOG
awk -F'\t' '$2!="*" {printf "  %-7s %s\n", $2, $3}' "$F" | head -n 10 >> $LOG
echo "--- 统计 ---" >> $LOG
T=$(awk -F'\t' '$2!="*"' "$F" | wc -l)
Q=$(awk -F'\t' '$2!="*" && $3=="?"' "$F" | wc -l)
echo "  UID 行 $T, pkg=? $Q" >> $LOG
echo "" >> $LOG
echo "--- 核算 ---" >> $LOG
"$D/plcollect" --status -f "$F" >> $LOG 2>&1

# 7. 重启 App
am force-stop $PKG
sleep 1
am start -n $PKG/.MainActivity >/dev/null 2>&1
echo "App 已重启" >> $LOG
echo "===== done =====" >> $LOG