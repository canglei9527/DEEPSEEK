#!/system/bin/sh
# tcount.sh -- 快速统计线程/进程（用 glob，不 fork），在 load 18 的机器上也秒出
# 用法: su -c "sh /data/local/tmp/tcount.sh"

echo "== 线程数 top 20 =="
for d in /proc/[0-9]*; do
  set -- "$d"/task/*
  n=$#
  [ "$n" -lt 60 ] && continue
  pid=${d#/proc/}
  nm=$(grep '^Name:' "$d/status" 2>/dev/null | cut -f2)
  echo "$n $pid $nm"
done | sort -nr | head -n 20 | while read n pid nm; do
  line=$(tr -d '\000' < /proc/$pid/cmdline 2>/dev/null | cut -c1-52)
  [ -n "$line" ] && nm="$nm ($line)"
  printf '  %-6s thread  pid=%-6s %s\n' "$n" "$pid" "$nm"
done

echo
echo "== 进程总数 =="
set -- /proc/[0-9]*
echo "  processes: $#"
t=0
for d in /proc/[0-9]*; do
  set -- "$d"/task/*
  t=$((t + $#))
done
echo "  threads:   $t"

echo
echo "== zram 详情 =="
cat /proc/swaps
echo -n '  mm_stat: '; cat /sys/block/zram0/mm_stat 2>/dev/null
echo

echo
echo "== 谁在频繁 fork（最近 15 个进程的启动者） =="
ps -A -o PID,PPID,NAME 2>/dev/null | awk 'NR>1{c[$2]++} END{for(p in c) if(c[p]>8) printf "  ppid=%-6s 子进程=%-4s\n", p, c[p]}' | head -n 12

echo
echo "== 内存与挂起 =="
grep -E 'MemFree|MemAvailable|SwapFree' /proc/meminfo
printf '  suspend fail=%s last=%s\n' "$(cat /sys/power/suspend_stats/fail 2>/dev/null)" "$(cat /sys/power/suspend_stats/last_failed_dev 2>/dev/null)"
cat /proc/pressure/memory 2>/dev/null