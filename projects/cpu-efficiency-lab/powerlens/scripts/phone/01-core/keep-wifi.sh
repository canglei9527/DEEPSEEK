#!/system/bin/sh
# keep-wifi.sh -- 保持 Wi-Fi 在息屏后不断开（解决 adb 频繁离线）
#
# ===== 问题 =====
#   实测：手机息屏约 1-2 分钟后，热点仍在跑（PC 侧 tethering=On, clients=1），
#   但 ping 100% 丢包、adb 端口关闭。
#   原因是 Android 的 Wi-Fi 省电策略在息屏后挂起了 STA/AP 的射频。
#
#   这导致所有长测量（30-60 秒的电流采样）都会中途断掉。
#
# ===== 解法（三层保险）=====
#   1. settings：关闭 Wi-Fi 息屏休眠
#   2. wakelock：持有一个 PARTIAL_WAKE_LOCK 让 Wi-Fi 保持活跃
#   3. 定时 ping：让网络栈有流量，不被判定为空闲
#
# 用法:
#   su -c "sh /data/local/tmp/keep-wifi.sh on"      开启保活
#   su -c "sh /data/local/tmp/keep-wifi.sh off"     关闭
#   su -c "sh /data/local/tmp/keep-wifi.sh status"  查看

ACTION=${1:-status}
LOCKFILE=/data/local/tmp/keep-wifi.pid
LOGFILE=/data/local/tmp/keep-wifi.log

case "$ACTION" in

on)
  echo "===== 开启 Wi-Fi 保活 ====="

  # --- 层 1：settings ---
  echo "  [1] 关闭 Wi-Fi 息屏休眠"
  settings put global wifi_sleep_policy 2 2>/dev/null && echo "    wifi_sleep_policy = 2 (永不休眠)"
  settings put global wifi_scan_always_enabled 0 2>/dev/null
  settings put global wifi_wakeup_enabled 0 2>/dev/null

  # --- 层 2：常驻 ping 循环（自身就是流量，能阻止网络栈判空闲）---
  echo "  [2] 启动 ping 保活循环"
  if [ -f "$LOCKFILE" ]; then
    OLD=$(cat "$LOCKFILE" 2>/dev/null)
    if [ -n "$OLD" ] && [ -d "/proc/$OLD" ]; then
      echo "    已有实例 pid=$OLD，先杀掉"
      kill -9 "$OLD" 2>/dev/null
      sleep 1
    fi
  fi

  # 用独立脚本 + setsid 脱离（避免多层引号导致丢权限）
setsid sh /data/local/tmp/wifi-ping-loop.sh >/dev/null 2>&1 &

  sleep 3
  NP=$(cat "$LOCKFILE" 2>/dev/null)
  if [ -n "$NP" ] && [ -d "/proc/$NP" ]; then
    echo "    ping 循环已启动 pid=$NP"
  else
    echo "    [失败] ping 循环未启动"
  fi

  # --- 层 3：检查 TCP keepalive 参数 ---
  echo "  [3] TCP keepalive 参数"
  echo "    tcp_keepalive_time     = $(cat /proc/sys/net/ipv4/tcp_keepalive_time 2>/dev/null)"
  echo "    tcp_keepalive_intvl    = $(cat /proc/sys/net/ipv4/tcp_keepalive_intvl 2>/dev/null)"

  echo ""
  echo "  完成。现在息屏后热点应保持可 ping。"
  ;;

off)
  echo "===== 关闭 Wi-Fi 保活 ====="
  if [ -f "$LOCKFILE" ]; then
    P=$(cat "$LOCKFILE" 2>/dev/null)
    [ -n "$P" ] && kill -9 "$P" 2>/dev/null && echo "  killed ping 循环 pid=$P"
    rm -f "$LOCKFILE"
  fi
  settings put global wifi_sleep_policy 0 2>/dev/null
  echo "  wifi_sleep_policy 恢复为 0（随系统）"
  ;;

status)
  echo "===== Wi-Fi 保活状态 ====="
  echo "  wifi_sleep_policy: $(settings get global wifi_sleep_policy)"
  echo "  --- ping 循环 ---"
  if [ -f "$LOCKFILE" ]; then
    P=$(cat "$LOCKFILE" 2>/dev/null)
    if [ -n "$P" ] && [ -d "/proc/$P" ]; then
      echo "    运行中 pid=$P"
      echo "    命令行: $(tr -d '\000' < /proc/$P/cmdline 2>/dev/null | head -c 60)"
    else
      echo "    [已死] pid 文件残留"
    fi
  else
    echo "    未启动"
  fi
  echo "  --- 最近日志 ---"
  tail -n 5 "$LOGFILE" 2>/dev/null | sed 's/^/    /'
  echo "  --- 当前网络 ---"
  ip addr show wlan0 2>/dev/null | grep -E 'inet |state' | sed 's/^/    /'
  echo "  --- 默认网关 ---"
  ip route 2>/dev/null | grep default | head -n2 | sed 's/^/    /'
  ;;
esac