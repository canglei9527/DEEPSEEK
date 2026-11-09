#!/system/bin/sh
# eptfd.sh -- 揪出带 EPOLLWAKEUP 的 timerfd 持有者
#   EPOLLWAKEUP = 0x20000000。在 /proc/PID/fdinfo/N 里，epoll fd 会列出
#   “tfd: <tfd> events: <8位hex> data: ...”，若 events 的最高半字节 >= 2，
#   说明这一路 epoll 注册了 EPOLLWAKEUP —— 它一超时就把设备从 s2idle 里踢出来。
# 用法: su -c "sh /data/local/tmp/eptfd.sh"

echo "===== 1. 所有带 EPOLLWAKEUP 的 fdinfo 文件 ====="
FILES=$(grep -lE 'events: [2-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F]' /proc/[0-9]*/fdinfo/* 2>/dev/null)
N=$(echo "$FILES" | grep -c . )
echo "  命中 fdinfo 文件数: $N"

echo
echo "===== 2. 按进程汇总（进程名 / 命中 fd 数） ====="
echo "$FILES" | sed 's|/proc/||; s|/fdinfo/.*||' | sort | uniq -c | sort -nr | head -n 30 | while read cnt pid; do
  nm=$(tr -d '\000' < /proc/$pid/cmdline 2>/dev/null | cut -c1-56)
  [ -z "$nm" ] && nm=$(grep '^Name:' /proc/$pid/status 2>/dev/null | cut -f2)
  printf '  %-4s fd  pid=%-7s %s\n' "$cnt" "$pid" "$nm"
done

echo
echo "===== 3. 命中明细（前 40 条） ====="
echo "$FILES" | head -n 40 | while read f; do
  pid=$(echo "$f" | cut -d/ -f3)
  fd=$(echo "$f" | cut -d/ -f5)
  nm=$(tr -d '\000' < /proc/$pid/cmdline 2>/dev/null | cut -c1-44)
  [ -z "$nm" ] && nm=$(grep '^Name:' /proc/$pid/status 2>/dev/null | cut -f2)
  echo "  pid=$pid fd=$fd $nm"
  grep -E 'tfd:' "$f" 2>/dev/null | sed 's/^/      /' | head -n 6
done

echo
echo "===== 4. 把 EPOLLWAKEUP 当“唯一来源”的候选：fd 数最多的进程 ====="
TOP=$(echo "$FILES" | sed 's|/proc/||; s|/fdinfo/.*||' | sort | uniq -c | sort -nr | head -n 5 | awk '{print $2}')
for pid in $TOP; do
  nm=$(tr -d '\000' < /proc/$pid/cmdline 2>/dev/null | cut -c1-56)
  [ -z "$nm" ] && nm=$(grep '^Name:' /proc/$pid/status 2>/dev/null | cut -f2)
  echo "  --- pid=$pid $nm"
  grep -hE 'tfd:' /proc/$pid/fdinfo/* 2>/dev/null | grep -E 'events: [2-9a-fA-F]' | head -n 8 | sed 's/^/      /'
done

echo
echo "===== 5. [timerfd] 这个唤醒源本身 ====="
head -n 1 /sys/kernel/debug/wakeup_sources 2>/dev/null
grep -E '^\[timerfd\]|^eventpoll|^event7' /sys/kernel/debug/wakeup_sources 2>/dev/null | sed 's/^/  /'

echo
echo "===== 6. alarmtimer 与近期 alarm ====="
dumpsys alarm 2>/dev/null | grep -E '^\s+(RTC|ELAPSED)_WAKEUP' | head -n 10 | sed 's/^/  /'
dumpsys alarm 2>/dev/null | grep -A3 'Top Alarms' | head -n 12 | sed 's/^/  /'