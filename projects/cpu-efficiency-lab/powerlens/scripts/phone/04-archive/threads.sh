#!/system/bin/sh
# threads.sh -- 找线程/进程爆炸的元凶（当前 1058 进程 / 13000+ 线程，load 18）
# 用法: su -c "sh /data/local/tmp/threads.sh"

echo "== 线程数 top 15 =="
for d in /proc/[0-9]*; do
  pid=${d#/proc/}
  [ -r "$d/status" ] || continue
  n=$(grep '^Threads:' "$d/status" 2>/dev/null | awk '{print $2}')
  [ -z "$n" ] && continue
  [ "$n" -lt 50 ] && continue
  c=$(tr -d '\000' < "$d/cmdline" 2>/dev/null | cut -c1-50)
  [ -z "$c" ] && c=$(grep '^Name:' "$d/status" | cut -f2)
  echo "$n $pid $c"
done | sort -nr | head -n 15 | awk '{printf "  threads=%-6s pid=%-7s %s\n", $1, $2, substr($0, index($0,$3))}'

echo
echo "== 进程数 top 10（按父进程统计） =="
ps -A -o PPID 2>/dev/null | tail -n +2 | sort | uniq -c | sort -nr | head -n 10

echo
echo "== 支付宝 / 米家 / 商店 进程数 =="
for k in AlipayGphone tencent.mm tencent.mobileqq vending aicr miui.home systemui; do
  n=$(ps -A -o NAME 2>/dev/null | grep -c "$k")
  echo "  $k: $n"
done

echo
echo "== wakeup_sources（debugfs 另一种路径） =="
for p in /sys/kernel/debug/wakeup_sources /d/wakeup_sources /sys/kernel/debug/power/wakeup_sources; do
  if [ -r "$p" ]; then
    echo "  [读自 $p]"
    head -n 1 "$p"
    tail -n +2 "$p" | sort -k7 -nr | head -n 15
    break
  fi
done

echo
echo "== dumpsys power 里的 wake lock =="
dumpsys power 2>/dev/null | sed -n '/Wake Locks/,/^$/p' | head -n 20

echo
echo "== Wi-Fi 状态 =="
dumpsys wifi 2>/dev/null | grep -iE 'Wi-Fi is|mNetworkInfo|SSID|RSSI|Link speed|frequency' | head -n 8

echo
echo "== CPU 时间 2 秒增量 top 10 =="
a=$(ps -A -o PID,TIME 2>/dev/null | tail -n +2)
sleep 2
b=$(ps -A -o PID,TIME 2>/dev/null | tail -n +2)
echo "$a" > /data/local/tmp/x1; echo "$b" > /data/local/tmp/x2
awk 'NR==FNR{t[$1]=$2; next} {if($1 in t){split(t[$1],p,":"); split($2,q,":"); o=p[1]*60+p[2]; n=q[1]*60+q[2]; if(n-o>0) printf "%d %d\n",$1,n-o}}' /data/local/tmp/x1 /data/local/tmp/x2 | sort -k2 -nr | head -n 10 | while read pid secs; do
  nm=$(tr -d '\000' < /proc/$pid/cmdline 2>/dev/null | cut -c1-55)
  [ -z "$nm" ] && nm=$(grep '^Name:' /proc/$pid/status 2>/dev/null | cut -f2)
  echo "  pid=$pid +${secs}s/2s  $nm"
done
rm -f /data/local/tmp/x1 /data/local/tmp/x2