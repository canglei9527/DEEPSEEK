#!/system/bin/sh
# plaudit.sh -- PowerLens 数据面审计：两个存储文件、*行、SELinux 标签、绝对值 vs 增量
# 用法: su -c "sh /data/local/tmp/plaudit.sh"

APP=/data/data/com.powerlens.app/files
TMPD=/data/local/tmp/powerlens

echo "===== 1. 两个存储文件 ====="
for f in "$APP/snapshots.tsv" "$TMPD/snapshots.tsv"; do
  echo "--- $f"
  ls -lZ "$f" 2>&1
  if [ -r "$f" ]; then
    echo "    lines=$(wc -l < $f)  bytes=$(wc -c < $f)"
    echo "    first_ts=$(head -n1 $f | cut -f1)  last_ts=$(tail -n1 $f | cut -f1)"
  fi
done

echo
echo "===== 2. '*' 电池行长什么样 ====="
for f in "$APP/snapshots.tsv" "$TMPD/snapshots.tsv"; do
  echo "--- $f"
  grep -n '^\S*\t\*' "$f" 2>/dev/null | head -n 5
  echo "    '*' 行数 = $(grep -c '^\S*\t\*' $f 2>/dev/null)"
done

echo
echo "===== 3. 每个时间戳的列数分布 ====="
for f in "$APP/snapshots.tsv" "$TMPD/snapshots.tsv"; do
  echo "--- $f"
  awk -F'\t' '{print NF}' "$f" 2>/dev/null | sort | uniq -c | sed 's/^/    /'
done

echo
echo "===== 4. 电池行的第 3 列（MAH）历史 ====="
for f in "$APP/snapshots.tsv" "$TMPD/snapshots.tsv"; do
  echo "--- $f"
  grep '^\S*\t\*' "$f" 2>/dev/null | awk -F'\t' '{printf "    ts=%s mah=%s\n", $1, $3}' | tail -n 12
done

echo
echo "===== 5. 时间戳间隔（秒） ====="
for f in "$APP/snapshots.tsv" "$TMPD/snapshots.tsv"; do
  echo "--- $f"
  grep '^\S*\t\*' "$f" 2>/dev/null | cut -f1 | awk 'NR>1{printf "    %d s\n", ($1-prev)/1000} {prev=$1}'
done

echo
echo "===== 6. 当前真实电量计 ====="
echo "  charge_counter(uAh) = $(cat /sys/class/power_supply/battery/charge_counter)"
echo "  capacity            = $(cat /sys/class/power_supply/battery/capacity)"
echo "  => mAh = $(( $(cat /sys/class/power_supply/battery/charge_counter) / 1000 ))"

echo
echo "===== 7. app 私有目录 SELinux 与权限 ====="
ls -ldZ "$APP" 2>&1
ls -lZ "$APP" 2>&1 | head -n 12

echo
echo "===== 8. app 端 status.txt 最近 12 条 ====="
tail -n 12 "$APP/status.txt" 2>/dev/null | sed 's/^/  /'

echo
echo "===== 9. app uid 与进程 ====="
dumpsys package com.powerlens.app 2>/dev/null | grep -E 'userId=|codePath' | sed 's/^/  /'
ps -A -o PID,NAME 2>/dev/null | grep powerlens | sed 's/^/  /'