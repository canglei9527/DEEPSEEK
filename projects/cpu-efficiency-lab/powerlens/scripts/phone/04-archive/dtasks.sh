#!/system/bin/sh
# dtasks.sh -- 谁卡在 D 态（这台机器常驻 14–15 个 D，load 18，这是不眠的真凶）
# 用法: su -c "sh /data/local/tmp/dtasks.sh"

echo "== D 态任务明细（含 wchan / stack） =="
for d in /proc/[0-9]*; do
  st=$(awk '{print $3}' "$d/stat" 2>/dev/null)
  [ "$st" = "D" ] || continue
  pid=${d#/proc/}
  nm=$(awk '{print $2}' "$d/stat" 2>/dev/null | tr -d '()')
  wc_=$(cat "$d/wchan" 2>/dev/null)
  echo "---- pid=$pid name=$nm wchan=$wc_"
  stk=$(cat "$d/stack" 2>/dev/null | head -n 6)
  if [ -n "$stk" ]; then
    echo "$stk" | sed 's/^/     /'
  else
    echo "     (stack 不可读)"
  fi
done

echo
echo "== D 态任务名汇总 =="
for d in /proc/[0-9]*; do
  st=$(awk '{print $3}' "$d/stat" 2>/dev/null)
  [ "$st" = "D" ] || continue
  awk '{print $2}' "$d/stat" 2>/dev/null | tr -d '()'
done | sort | uniq -c | sort -nr

echo
echo "== 全部内核线程（[] 包起来的）前 40 =="
ps -A -o PID,PPID,NAME 2>/dev/null | grep '\[' | head -n 40

echo
echo "== 有多少 kworker 常驻 =="
ps -A -o NAME 2>/dev/null | grep -c '^\[kworker'

echo
echo "== task 总数 (kernel.max_pid / threads-max) =="
cat /proc/sys/kernel/threads-max 2>/dev/null
cat /proc/sys/kernel/pid_max 2>/dev/null

echo
echo "== block 层队列状态（I/O 卡住会让人一直 D） =="
for q in /sys/block/*/queue/nr_requests; do
  b=$(echo "$q" | cut -d/ -f4)
  echo "  $b nr_requests=$(cat $q 2>/dev/null)"
done
echo "--- /proc/diskstats 里 ufs ---"
grep -E 'sd[a-z]|mmcblk|nvme' /proc/diskstats 2>/dev/null | head -n 6