#!/system/bin/sh
# dsu.sh -- 追查 com.android.dynsystem 与 iowait
# 用法: su -c "sh /data/local/tmp/dsu.sh"

echo "########## 1. dynsystem 是什么 ##########"
dumpsys package com.android.dynsystem 2>/dev/null | grep -E 'versionName|codePath|firstInstallTime|userId|flags' | head -n 8 | sed 's/^/  /'

echo
echo "########## 2. 有 DSU 安装吗 ##########"
echo "--- gsi 相关 ---"
ls -l /data/gsi /data/gsi/dsu 2>/dev/null | sed 's/^/  /'
adb_dsu=$(dumpsys activity service com.android.dynsystem 2>/dev/null | head -n 20)
echo "$adb_dsu" | sed 's/^/  /'

echo
echo "--- 已安装的 DSU 分区 ---"
cmd dynamic-system 2>/dev/null | head -n 20 | sed 's/^/  /' || echo "  (cmd dynamic-system 不可用)"

echo
echo "########## 3. 挂载点（是否有 GSI 挂载） ##########"
mount 2>/dev/null | grep -iE 'gsi|dsu|system_dlkm' | sed 's/^/  /'
echo "--- /proc/mounts 里的 /data 相关 ---"
grep -E ' /data | /data\b' /proc/mounts 2>/dev/null | head -n 5 | sed 's/^/  /'

echo
echo "########## 4. dynsystem 进程现状 ##########"
ps -A -o PID,PPID,STAT,RSS,ARGS 2>/dev/null | grep -i dynsystem | grep -v grep | sed 's/^/  /'
PID=$(pidof com.android.dynsystem 2>/dev/null)
if [ -n "$PID" ]; then
  echo "  --- VmRSS / Threads ---"
  grep -E 'VmRSS|VmSwap|Threads' /proc/$PID/status | sed 's/^/    /'
  echo "  --- 打开的 /data 相关 fd（前 10） ---"
  ls -l /proc/$PID/fd 2>/dev/null | grep -E '/data|\.img|loop' | head -n 10 | sed 's/^/    /'
  echo "  --- 线程名 ---"
  for t in /proc/$PID/task/*; do cat $t/comm 2>/dev/null; done | sort | uniq -c | sort -nr | head -n 10 | sed 's/^/    /'
fi

echo
echo "########## 5. iowait 与磁盘 I/O ##########"
echo "--- /proc/stat 15 秒 iowait ---"
awk '/^cpu /{printf "%d %d %d\n", $2+$3+$4+$5+$6+$7+$8+$9, $5+$6, $2+$3}' /proc/stat > /tmp/io0
sleep 15
awk '/^cpu /{printf "%d %d %d\n", $2+$3+$4+$5+$6+$7+$8+$9, $5+$6, $2+$3}' /proc/stat > /tmp/io1
awk 'FNR==NR{t1=$1;i1=$2;u1=$3;next}{dt=$1-t1; di=$2-i1; du=$3-u1
  if(dt>0) printf "  iowait=%.1f%%  user+sys=%.1f%%  (总 %d jiffies)\n", di*100/dt, du*100/dt, dt}' /tmp/io0 /tmp/io1

echo
echo "--- 块设备累计 I/O ---"
awk 'NF>=14 && ($3 ~ /^(sd|mmcblk|nvme|dm-|loop)/) {
  printf "  %-12s 读扇区=%-14s 写扇区=%-14s I/O耗时(ms)=%s\n", $3, $6, $10, $13
}' /proc/diskstats 2>/dev/null | head -n 12

echo
echo "--- 15 秒块设备增量 ---"
awk 'NF>=14 {print $3, $6, $10}' /proc/diskstats > /tmp/ds0
sleep 15
awk 'NF>=14 {print $3, $6, $10}' /proc/diskstats > /tmp/ds1
awk 'FNR==NR{r[$1]=$2;w[$1]=$3;next}{
  if ($1 in r) {
    dr=$2-r[$1]; dw=$3-w[$1]
    if (dr>0 || dw>0) printf "  %-14s +读 %d 扇区(%.1f MB)  +写 %d 扇区(%.1f MB)\n", $1, dr, dr*512/1048576, dw, dw*512/1048576
  }
}' /tmp/ds0 /tmp/ds1 | head -n 12

echo
echo "########## 6. 谁在大量写 /data ##########"
awk '/ \/data /{print}' /proc/mounts 2>/dev/null
echo "--- 当前 D 态任务 ---"
ps -A -o PID,STAT,NAME 2>/dev/null | awk '$2 ~ /^D/ {printf "  %s %s %s\n", $1, $2, $3}'

echo
echo "########## 7. dynsystem 的定时任务/服务 ##########"
dumpsys activity services com.android.dynsystem 2>/dev/null | head -n 15 | sed 's/^/  /'
logcat -d 2>/dev/null | grep -iE 'dynsystem|Dsu|gsi' | tail -n 15 | sed 's/^/  /'