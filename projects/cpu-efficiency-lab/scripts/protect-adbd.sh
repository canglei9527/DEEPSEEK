#!/system/bin/sh
# protect-adbd.sh -- 防 LMK 杀 adbd
# 把 adbd 的 oom_adj 设为 -17（最低优先级，LMK 不会杀）

LOG=/data/local/tmp/eeff/protect-adbd.log
log() { echo "$(date '+%H:%M:%S') $@" >> "$LOG"; }

# 找 adbd 的 PID
ADBD_PID=$(ps -A | grep "adbd" | awk '{print $2}' | head -1)
if [ -z "$ADBD_PID" ]; then
  log "adbd not found"
  exit 1
fi

log "adbd pid=$ADBD_PID"
# 设置 oom_adj 为 -17（最低，LMK 不杀）
echo -17 > /proc/$ADBD_PID/oom_adj 2>/dev/null
echo -17 > /proc/$ADBD_PID/oom_score_adj 2>/dev/null
log "oom_adj set to -17"

# 同时保护 watchdog 自己
echo -17 > /proc/$$/oom_adj 2>/dev/null
echo -17 > /proc/$$/oom_score_adj 2>/dev/null

# 监控循环：如果 adbd 死了就重启
while true; do
  if ! ps -A | grep -q "adbd"; then
    log "adbd dead, restarting..."
    # 用 setprop 重启 adbd（不需要 stop/start）
    setprop ctl.restart adbd 2>/dev/null
    sleep 3
    ADBD_PID=$(ps -A | grep "adbd" | awk '{print $2}' | head -1)
    if [ -n "$ADBD_PID" ]; then
      echo -17 > /proc/$ADBD_PID/oom_adj 2>/dev/null
      echo -17 > /proc/$ADBD_PID/oom_score_adj 2>/dev/null
      log "adbd restarted, pid=$ADBD_PID"
    else
      log "adbd restart FAILED"
    fi
  fi
  sleep 5
done
