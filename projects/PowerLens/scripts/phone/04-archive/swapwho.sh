#!/system/bin/sh
# swapwho.sh -- 找出把 zram 吃满的进程（swap 已用 11.4 GB / 16 GB）
# 用法: su -c "sh /data/local/tmp/swapwho.sh"

echo "===== zram 使用 ====="
cat /proc/swaps
echo -n 'mm_stat: '; cat /sys/block/zram0/mm_stat 2>/dev/null
echo

echo "===== 按 VmSwap 排序 top 20 ====="
printf '%-8s %-10s %s\n' PID SWAP_KB NAME
for d in /proc/[0-9]*; do
  pid=${d#/proc/}
  [ -r "$d/status" ] || continue
  s=$(grep '^VmSwap:' "$d/status" 2>/dev/null | awk '{print $2}')
  [ -z "$s" ] && continue
  if [ "$s" -ge 4096 ]; then
    c=$(tr -d '\000' < "$d/cmdline" 2>/dev/null | cut -c1-50)
    if [ -z "$c" ]; then c=$(grep '^Name:' "$d/status" | cut -f2); fi
    echo "$pid $s $c"
  fi
done | sort -k2 -nr | head -n 20

echo
echo "===== 按 VmRSS 排序 top 20 ====="
printf '%-8s %-10s %s\n' PID RSS_KB NAME
for d in /proc/[0-9]*; do
  pid=${d#/proc/}
  [ -r "$d/status" ] || continue
  s=$(grep '^VmRSS:' "$d/status" 2>/dev/null | awk '{print $2}')
  [ -z "$s" ] && continue
  if [ "$s" -ge 20000 ]; then
    c=$(tr -d '\000' < "$d/cmdline" 2>/dev/null | cut -c1-50)
    if [ -z "$c" ]; then c=$(grep '^Name:' "$d/status" | cut -f2); fi
    echo "$pid $s $c"
  fi
done | sort -k2 -nr | head -n 20

echo
echo "===== 进程总数 / 线程总数 ====="
echo -n 'processes: '; ls -d /proc/[0-9]* 2>/dev/null | wc -l
echo -n 'threads:   '; ls -d /proc/[0-9]*/task/* 2>/dev/null | wc -l

echo
echo "===== 支付宝进程清单 ====="
ps -A -o PID,PPID,RSS,NAME 2>/dev/null | grep -i alipay

echo
echo "===== 其他大内存应用清单 ====="
ps -A -o PID,RSS,NAME 2>/dev/null | awk '$2>150000{print}' | sort -k2 -nr | head -n 15