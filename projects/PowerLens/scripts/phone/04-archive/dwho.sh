#!/system/bin/sh
# dwho.sh -- 只读 /proc/PID/stat 第3列 + wchan，绝不碰 /proc/PID/stack
# （读 D 态任务的 stack 会一起卡死，这是本机踩过的坑）
# 用法: su -c "sh /data/local/tmp/dwho.sh"

echo "== D 态任务清单 =="
for d in /proc/[0-9]*; do
  st=$(awk '{print $3}' "$d/stat" 2>/dev/null)
  [ "$st" = "D" ] || continue
  pid=${d#/proc/}
  nm=$(awk '{print $2}' "$d/stat" 2>/dev/null | tr -d '()')
  wc_=$(cat "$d/wchan" 2>/dev/null)
  ppid=$(awk '{print $4}' "$d/stat" 2>/dev/null)
  printf '  pid=%-7s ppid=%-7s wchan=%-28s %s\n' "$pid" "$ppid" "$wc_" "$nm"
done

echo
echo "== D 态按名字汇总 =="
for d in /proc/[0-9]*; do
  st=$(awk '{print $3}' "$d/stat" 2>/dev/null)
  [ "$st" = "D" ] || continue
  awk '{print $2}' "$d/stat" 2>/dev/null | tr -d '()'
done | sort | uniq -c | sort -nr

echo
echo "== D 态按 wchan 汇总 =="
for d in /proc/[0-9]*; do
  st=$(awk '{print $3}' "$d/stat" 2>/dev/null)
  [ "$st" = "D" ] || continue
  cat "$d/wchan" 2>/dev/null; echo
done | sort | uniq -c | sort -nr

echo
echo "== 常驻 kworker 数量 =="
ps -A -o NAME 2>/dev/null | grep -c '^\[kworker'

echo
echo "== threads-max / pid_max =="
printf '  threads-max=%s pid-max=%s\n' "$(cat /proc/sys/kernel/threads-max 2>/dev/null)" "$(cat /proc/sys/kernel/pid_max 2>/dev/null)"

echo
echo "== UFS / 存储 I/O =="
grep -E 'sd[a-z] |mmcblk|nvme' /proc/diskstats 2>/dev/null | head -n 8

echo
echo "== io 压力 =="
cat /proc/pressure/io 2>/dev/null