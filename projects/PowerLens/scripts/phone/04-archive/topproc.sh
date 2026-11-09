#!/system/bin/sh
# topproc.sh -- 按 RSS / CPU时间 排序的真实进程名（top 的精简列在 HyperOS 上会被截断）
# 用法: su -c "sh /data/local/tmp/topproc.sh"

echo "===== MEM ====="
free -m 2>/dev/null || cat /proc/meminfo | head -n 5

echo
echo "===== TOP 15 by RSS (MB) ====="
printf '%-8s %-8s %-10s %s\n' PID RSS_MB UTIME_STIME NAME
for p in /proc/[0-9]*; do
    pid=${p#/proc/}
    [ -r "$p/statm" ] || continue
    rss=$(awk '{print $2}' "$p/statm" 2>/dev/null)
    [ -z "$rss" ] && continue
    cmd=$(tr -d '\0' < "$p/cmdline" 2>/dev/null | cut -c1-60)
    [ -z "$cmd" ] && cmd=$(awk '{print $2}' "$p/stat" 2>/dev/null | tr -d '()')
    echo "$pid $((rss * 4 / 1024)) $cmd"
done | sort -k2 -nr | head -n 15

echo
echo "===== TOP 15 by CPU ticks (utime+stime) ====="
for p in /proc/[0-9]*; do
    pid=${p#/proc/}
    [ -r "$p/stat" ] || continue
    set -- $(awk '{for(i=1;i<=NF;i++) if($i ~ /^[0-9]+$/ && i>2){print; break}}' "$p/stat" 2>/dev/null)
    ticks=$(awk '{print $14+$15}' "$p/stat" 2>/dev/null)
    [ -z "$ticks" ] && continue
    cmd=$(tr -d '\0' < "$p/cmdline" 2>/dev/null | cut -c1-60)
    [ -z "$cmd" ] && cmd=$(awk '{print $2}' "$p/stat" 2>/dev/null | tr -d '()')
    echo "$pid $ticks $cmd"
done | sort -k2 -nr | head -n 15

echo
echo "===== TOP 10 by 最近 2s CPU 占用 ====="
a=$(for p in /proc/[0-9]*; do pid=${p#/proc/}; t=$(awk '{print $14+$15}' "$p/stat" 2>/dev/null); [ -n "$t" ] && echo "$pid $t"; done)
sleep 2
b=$(for p in /proc/[0-9]*; do pid=${p#/proc/}; t=$(awk '{print $14+$15}' "$p/stat" 2>/dev/null); [ -n "$t" ] && echo "$pid $t"; done)
echo "$a" > /data/local/tmp/_a.txt; echo "$b" > /data/local/tmp/_b.txt
join /data/local/tmp/_a.txt /data/local/tmp/_b.txt | awk '{d=$3-$2; if(d>0) printf "%d %d\n", $1, d*100/200}' \
  | sort -k2 -nr | head -n 10 | while read pid pct; do
      cmd=$(tr -d '\0' < /proc/$pid/cmdline 2>/dev/null | cut -c1-60)
      [ -z "$cmd" ] && cmd=$(awk '{print $2}' /proc/$pid/stat 2>/dev/null | tr -d '()')
      printf '%-8s %3s%%  %s\n' "$pid" "$pct" "$cmd"
  done
rm -f /data/local/tmp/_a.txt /data/local/tmp/_b.txt

echo
echo "===== 内核线程 CPU 大户（kworker 等） ====="
for pid in $(ps -A -o PID,NAME 2>/dev/null | awk '/\[?kworker|ksoftirqd|irq\//{print $1}'); do
    t=$(awk '{print $14+$15}' /proc/$pid/stat 2>/dev/null)
    n=$(tr -d '\0' < /proc/$pid/cmdline 2>/dev/null)
    [ -z "$n" ] && n=$(awk '{print $2}' /proc/$pid/stat 2>/dev/null | tr -d '()')
    [ -n "$t" ] && echo "$t $n"
done | sort -nr | head -n 12