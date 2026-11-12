#!/system/bin/sh
# adbd-watchdog.sh -- 监控 adbd，死了就自动重启无线调试
# 解决：内存不足时 LMK 杀 adbd 导致无线调试断连

LOG=/data/local/tmp/eeff/adbd-watchdog.log
PORT_FILE=/data/local/tmp/eeff/adbd-port.txt

log() { echo "$(date '+%H:%M:%S') $@" >> "$LOG"; }

# 从 settings 读当前无线调试端口（如果用户开了）
get_port() {
  settings get global adb_wifi_port 2>/dev/null || echo ""
}

# 检查 adbd 是否响应
adbd_alive() {
  # 用 adb 本地 shell 测试（不需要网络）
  ps -A | grep -q "adbd" && return 0 || return 1
}

# 重启无线调试
restart_adbd() {
  log "adbd dead, restarting..."
  # 方法1: 重启 adbd 服务
  stop adbd 2>/dev/null
  sleep 1
  start adbd 2>/dev/null
  sleep 2
  # 方法2: 如果还是不行，重新 enable 无线调试
  if ! adbd_alive; then
    log "adbd still dead, re-enable wireless debugging"
    settings put global adb_wifi_enabled 0 2>/dev/null
    sleep 1
    settings put global adb_wifi_enabled 1 2>/dev/null
    sleep 3
  fi
  if adbd_alive; then
    log "adbd restarted successfully"
  else
    log "adbd restart FAILED"
  fi
}

log "watchdog started"
while true; do
  if ! adbd_alive; then
    restart_adbd
  fi
  sleep 10
done
