#!/system/bin/sh
# dupchk.sh -- 查重复采集器（service.d / daemon.sh / JobScheduler 可能同时在写）
# 用法: su -c "sh /data/local/tmp/dupchk.sh"

echo "########## 1. service.d 里的 PowerLens 采集 ##########"
ls -l /data/adb/service.d/ 2>/dev/null | sed 's/^/  /'
echo
echo "--- 30-powerlens.sh 内容 ---"
cat /data/adb/service.d/30-powerlens.sh 2>/dev/null | sed 's/^/  /'

echo
echo "########## 2. /data/local/tmp/powerlens/daemon.sh 是否在跑 ##########"
ps -A -o PID,PPID,ARGS 2>/dev/null | grep -E 'daemon\.sh|pass\.sh' | grep -v grep | sed 's/^/  /'
echo "  (空=没在跑)"

echo
echo "########## 3. 所有可能写 snapshots.tsv 的进程 ##########"
for PID in $(ls /proc | grep -E '^[0-9]+$'); do
  for fd in /proc/$PID/fd/*; do
    tgt=$(readlink "$fd" 2>/dev/null)
    case "$tgt" in
      *snapshots.tsv*)
        nm=$(tr -d '\000' < /proc/$PID/cmdline 2>/dev/null | cut -c1-70)
        echo "  pid=$PID fd=$(basename $fd) -> $tgt"
        echo "       cmd=$nm"
        ;;
    esac
  done
done

echo
echo "########## 4. JobScheduler 里 PowerLens 的周期 ##########"
dumpsys jobscheduler 2>/dev/null | grep -A10 'JOB #u0a427/4711' | grep -E 'PERIODIC|Service|Required' | sed 's/^/  /'

echo
echo "########## 5. snapshots.tsv 的两次采集间隔（最近 12 条 battery 行） ##########"
F=/data/data/com.powerlens.app/files/snapshots.tsv
if [ -r "$F" ]; then
  grep '\*battery\*' "$F" 2>/dev/null | tail -n 12 | cut -f1 | awk 'NR>1{printf "    %d s\n", ($1-prev)/1000} {prev=$1}'
  echo "  总行数=$(wc -l < $F)"
fi

echo
echo "########## 6. app 是否被冻结/待机限制 ##########"
dumpsys deviceidle 2>/dev/null | grep -iE 'powerlens|whitelist' | head -n 8 | sed 's/^/  /'
echo "--- standby bucket ---"
am get-standby-bucket com.powerlens.app 2>/dev/null | sed 's/^/  /'