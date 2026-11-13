#!/system/bin/sh
# selfcheck.sh -- 手机端自主验证（不依赖 adb，写完结果到自己文件里）
# 用法: su -c "setsid sh /data/local/tmp/selfcheck.sh >/dev/null 2>&1 &"
# 输出: /data/local/tmp/plc/selfcheck.log

D=/data/local/tmp/plc
LOG=$D/selfcheck.log
F=/data/data/com.powerlens.app/files/snapshots.tsv

: > $LOG
echo "===== selfcheck $(date '+%F %T') =====" >> $LOG

echo "" >> $LOG
echo "## 1. plcollect 版本" >> $LOG
ls -l "$D/plcollect" >> $LOG 2>&1
echo -n "  含 packages.list 补全: " >> $LOG
strings "$D/plcollect" 2>/dev/null | grep -c 'packages.list' >> $LOG

echo "" >> $LOG
echo "## 2. 采集一次到独立文件" >> $LOG
"$D/plcollect" --once -f "$D/probe2.tsv" >> $LOG 2>&1

TOTAL=$(awk -F'\t' '$2!="*"' "$D/probe2.tsv" 2>/dev/null | wc -l)
QM=$(awk -F'\t' '$2!="*" && $3=="?"' "$D/probe2.tsv" 2>/dev/null | wc -l)
echo "  UID 行总数: $TOTAL" >> $LOG
echo "  pkg=? 行数: $QM" >> $LOG

echo "" >> $LOG
echo "## 3. 包名样例（前 15）" >> $LOG
awk -F'\t' '$2!="*" {printf "  uid=%-6s %s\n", $2, $3}' "$D/probe2.tsv" 2>/dev/null | head -n 15 >> $LOG

echo "" >> $LOG
echo "## 4. 守护进程" >> $LOG
ps -A -o PID,ARGS 2>/dev/null | grep plc-daemon | grep -v grep >> $LOG 2>&1

echo "" >> $LOG
echo "## 5. 数据文件健康度" >> $LOG
if [ -r "$F" ]; then
  echo "  行数: $(wc -l < $F)" >> $LOG
  echo "  --- 电池行格式 ---" >> $LOG
  awk -F'\t' '$2=="*" {
    if ($4 > 100000) print "    µAh(新)"
    else if ($4 > 0) print "    mAh(旧)"
    else             print "    空"
  }' "$F" | sort | uniq -c >> $LOG 2>&1
  echo "  --- pkg=? 比例 ---" >> $LOG
  T=$(awk -F'\t' '$2!="*"' "$F" | wc -l)
  Q=$(awk -F'\t' '$2!="*" && $3=="?"' "$F" | wc -l)
  echo "    总数 $T，问号 $Q" >> $LOG
else
  echo "  数据文件不存在" >> $LOG
fi

echo "" >> $LOG
echo "## 6. 核算" >> $LOG
"$D/plcollect" --status -f "$F" >> $LOG 2>&1

echo "" >> $LOG
echo "## 7. 当前电池" >> $LOG
for f in status capacity charge_counter current_now; do
  printf '  %-16s %s\n' "$f" "$(cat /sys/class/power_supply/battery/$f 2>/dev/null)" >> $LOG
done

echo "" >> $LOG
echo "## 8. 屏幕" >> $LOG
dumpsys power 2>/dev/null | grep -o 'mWakefulness=[A-Za-z]*' >> $LOG
cat /sys/class/backlight/panel0-backlight/brightness 2>/dev/null | sed 's/^/  backlight=/' >> $LOG

echo "" >> $LOG
echo "===== done =====" >> $LOG