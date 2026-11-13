#!/system/bin/sh
# dstat.sh -- 只用 ps（内核一次给全表），绝不逐个读 /proc，避免读到 D 态任务时把自己也卡住
# 用法: su -c "sh /data/local/tmp/dstat.sh"

echo "== D 态任务（ps 视角） =="
ps -A -o PID,PPID,STAT,NAME 2>/dev/null | awk '$3 ~ /^D/ {printf "  pid=%-7s ppid=%-7s stat=%-4s %s\n", $1, $2, $3, $4}'

echo
echo "== D 态按名字汇总 =="
ps -A -o STAT,NAME 2>/dev/null | awk '$1 ~ /^D/ {print $2}' | sort | uniq -c | sort -nr

echo
echo "== 各 STAT 首字母分布（全表） =="
ps -A -o STAT 2>/dev/null | tail -n +2 | cut -c1 | sort | uniq -c | sort -nr

echo
echo "== 总数 =="
ps -A -o PID 2>/dev/null | tail -n +2 | wc -l

echo
echo "== loadavg =="
cat /proc/loadavg

echo
echo "== 压力 =="
cat /proc/pressure/cpu 2>/dev/null
cat /proc/pressure/io 2>/dev/null
cat /proc/pressure/memory 2>/dev/null