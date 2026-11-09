#!/system/bin/sh
# cpuinfo.sh -- HyperOS 自带的 CPU 归因（比 top 好读），外加进程名直方图
# 用法: su -c "sh /data/local/tmp/cpuinfo.sh"

echo "== dumpsys cpuinfo =="
dumpsys cpuinfo 2>/dev/null | head -n 30

echo
echo "== 进程名直方图 top 20（谁开了很多进程） =="
ps -A -o NAME 2>/dev/null | tail -n +2 | sed 's/:.*//' | sort | uniq -c | sort -nr | head -n 20

echo
echo "== 僵尸进程 =="
Z=$(ps -A -o PID,STAT,NAME 2>/dev/null | awk '$2 ~ /^Z/')
if [ -n "$Z" ]; then echo "$Z" | head -n 20; echo "  僵尸总数: $(echo "$Z" | wc -l)"; else echo "  无"; fi

echo
echo "== 进程/线程总数（glob 计数） =="
set -- /proc/[0-9]*
P=$#
T=0
for d in /proc/[0-9]*; do
  set -- "$d"/task/*
  T=$((T + $#))
done
echo "  processes=$P  threads=$T"

echo
echo "== loadavg =="
cat /proc/loadavg

echo
echo "== 每分钟 blocked 任务 =="
cat /proc/pressure/cpu 2>/dev/null
cat /proc/pressure/memory 2>/dev/null

echo
echo "== 屏幕/活跃状态 =="
dumpsys power 2>/dev/null | grep -E 'mWakefulness=|mScreenOffTimeoutSetting'