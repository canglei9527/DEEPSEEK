#!/system/bin/sh
# tfds.sh -- 找出谁在用 timerfd 把设备从 s2idle 里踢出来（[timerfd] 占中止 82%）
# 用法: su -c "sh /data/local/tmp/tfds.sh"

echo "== debugfs 挂载情况 =="
mount 2>/dev/null | grep -E 'debugfs|tracefs'
ls -ld /sys/kernel/debug 2>/dev/null

echo
echo "== 尝试挂 debugfs（只读） =="
if [ ! -r /sys/kernel/debug/wakeup_sources ]; then
  mount -t debugfs none /sys/kernel/debug 2>&1 | head -n 2
fi
if [ -r /sys/kernel/debug/wakeup_sources ]; then
  echo "  OK: 可读"
else
  echo "  仍不可读"
fi

echo
echo "== wakeup_sources: active 的源 =="
if [ -r /sys/kernel/debug/wakeup_sources ]; then
  head -n 1 /sys/kernel/debug/wakeup_sources
  echo '--- 按 active_count 倒序 top 20 ---'
  tail -n +2 /sys/kernel/debug/wakeup_sources | sort -k6 -nr | head -n 20
fi

echo
echo "== 持有 timerfd 的进程（fdinfo 里有 tfd:） =="
grep -l 'tfd:' /proc/[0-9]*/fdinfo/* 2>/dev/null > /data/local/tmp/_tfd.txt
LAST=""
while read -r f; do
  pid=$(echo "$f" | cut -d/ -f3)
  [ "$pid" = "$LAST" ] && continue
  LAST="$pid"
  nm=$(tr -d '\000' < /proc/$pid/cmdline 2>/dev/null | cut -c1-58)
  [ -z "$nm" ] && nm=$(grep '^Name:' /proc/$pid/status 2>/dev/null | cut -f2)
  echo "  pid=$pid  $nm"
done < /data/local/tmp/_tfd.txt

echo
echo "== timerfd 的 fdinfo 明细（前 40 条） =="
grep -H 'tfd:' /proc/[0-9]*/fdinfo/* 2>/dev/null | head -n 40

echo
echo "== 带 EPOLLWAKEUP 的 epoll 条目 =="
grep -H 'tfd:' /proc/[0-9]*/fdinfo/* 2>/dev/null | grep -i 'events:.*[2-9a-f]\{8\}' | head -n 20

rm -f /data/local/tmp/_tfd.txt