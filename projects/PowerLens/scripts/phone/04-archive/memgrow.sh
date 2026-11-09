#!/system/bin/sh
# memgrow.sh -- 观察 PowerLens 进程内存增长曲线
# 用法: su -c "sh /data/local/tmp/memgrow.sh"

echo "== 冷启动 =="
am force-stop com.powerlens.app
sleep 2
am start -n com.powerlens.app/.MainActivity > /dev/null 2>&1

echo
printf '  %-6s %-10s %-10s %-8s %s\n' 'T(s)' 'VmRSS_kB' 'VmSwap_kB' 'Threads' 'JavaHeap_kB'
i=0
while [ $i -le 60 ]; do
  PID=$(pidof com.powerlens.app 2>/dev/null)
  if [ -n "$PID" ]; then
    RSS=$(grep '^VmRSS:' /proc/$PID/status 2>/dev/null | awk '{print $2}')
    SW=$(grep '^VmSwap:' /proc/$PID/status 2>/dev/null | awk '{print $2}')
    TH=$(grep '^Threads:' /proc/$PID/status 2>/dev/null | awk '{print $2}')
    JH=$(dumpsys meminfo com.powerlens.app 2>/dev/null | grep 'Dalvik Heap' | head -n1 | awk '{print $(NF-1)}')
    printf '  %-6s %-10s %-10s %-8s %s\n' "$i" "${RSS:-?}" "${SW:-?}" "${TH:-?}" "${JH:-?}"
  else
    printf '  %-6s (进程不在了)\n' "$i"
  fi
  sleep 10
  i=$((i+10))
done