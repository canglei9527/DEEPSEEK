#!/system/bin/sh
# dup2.sh -- 精简版重复采集检查（不做全 fd 遍历）
# 用法: su -c "sh /data/local/tmp/dup2.sh"

echo "########## 1. service.d ##########"
ls -l /data/adb/service.d/ 2>/dev/null | sed 's/^/  /'
echo "--- 30-powerlens.sh ---"
cat /data/adb/service.d/30-powerlens.sh 2>/dev/null | sed 's/^/  /'

echo
echo "########## 2. daemon.sh 进程 ##########"
ps -A 2>/dev/null | grep -E 'daemon\.sh|pass\.sh|powerlens' | grep -v grep | sed 's/^/  /'

echo
echo "########## 3. lsof 风格：谁打开了 snapshots.tsv ##########"
for PID in $(ls /proc | grep -E '^[0-9]+$'); do
  ls -l /proc/$PID/fd 2>/dev/null | grep -q 'snapshots.tsv' && {
    nm=$(tr -d '\000' < /proc/$PID/cmdline 2>/dev/null | cut -c1-60)
    [ -z "$nm" ] && nm=$(grep '^Name:' /proc/$PID/status 2>/dev/null | cut -f2)
    echo "  pid=$PID  $nm"
  }
done

echo
echo "########## 4. JobScheduler 周期 ##########"
dumpsys jobscheduler 2>/dev/null | grep -A12 'JOB #u0a427/4711' | grep -E 'PERIODIC|Service:' | sed 's/^/  /'

echo
echo "########## 5. 采集间隔 ##########"
F=/data/data/com.powerlens.app/files/snapshots.tsv
grep '\*battery\*' "$F" 2>/dev/null | tail -n 10 | cut -f1 | awk 'NR>1{printf "    %d s\n", ($1-prev)/1000} {prev=$1}'
echo "  总行数=$(wc -l < $F 2>/dev/null)"

echo
echo "########## 6. standby bucket / 白名单 ##########"
am get-standby-bucket com.powerlens.app 2>/dev/null | sed 's/^/  bucket=/'