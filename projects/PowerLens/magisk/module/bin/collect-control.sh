#!/system/bin/sh
# collect-control.sh -- 采集开关 + 空闲自动暂停
#
# ===== 为什么需要它 =====
#   常驻采集本身是纯开销：每 60 秒 fork 两次 su、读 procfs、写 tsV。
#   用户不需要看电量曲线时（例如已经知道结果、或只想知道"今天大概用了多少"），
#   这部分开销没有收益。所以提供三种状态：
#
#     on      正常采集
#     off     完全停止（守护退出，不再产生任何开销）
#     auto    闲置一段时间后自动停，屏幕亮起后自动恢复
#
# ===== auto 的判定为什么用屏幕状态 =====
#   功耗归因关心的是"用户在用手机时的耗电"。屏幕熄灭期间系统大量休眠，
#   采集到的数据价值很低，而唤醒 CPU 本身就在破坏这个休眠。
#   实测本机熄屏后采集仍每 60 秒唤醒一次，这在待机功耗测试里是直接污染。
#
#   所以 auto 模式：熄屏持续 N 分钟 → 停采；亮屏 → 立即恢复。
#   N 默认 10 分钟，可在 collect.conf 里改。
#
# ===== 状态文件 =====
#   /data/local/tmp/plc/collect.conf   on | off | auto
#   /data/local/tmp/plc/collect.lock   off 时存在，守护脚本据此不自启
#   /data/local/tmp/plc/collect.state  人类可读的当前状态（App 展示用）
#
# ===== 用法 =====
#   sh collect-control.sh on|off|auto|status
#   svc power get-mode 用来读屏幕状态

PL_TMP=/data/local/tmp/plc
LOG=$PL_TMP/daemon.log
CONF=$PL_TMP/collect.conf
STATE=$PL_TMP/collect.state
LOCK=$PL_TMP/collect.lock
DAEMON=$PL_TMP/plc-daemon.sh

CPU_INT=60
DIM_INT=1200

say() {
  echo "$(date '+%F %T') collect: $*" >> "$LOG" 2>/dev/null
}

write_state() {
  # App 以普通 uid 读取，必须 666
  {
    echo "mode=$1"
    echo "running=$2"
    echo "since=$(date +%s)"
    echo "reason=$3"
  } > "$STATE" 2>/dev/null
  chmod 666 "$STATE" 2>/dev/null
}

# ===== 实例判定 =====
#
# 不能只 grep cmdline 里的脚本名：我们是用
#     su -c "sh /data/local/tmp/plc/collect-control.sh off"
# 调用的，外层包装进程的 cmdline 也带脚本名，会被误判成"已有实例"。
# 也不能只看 /proc/<pid> 存在：zombie 的 /proc 目录仍在。
#
# 所以：proc 非 zombie，且 cmdline 的 arg2 正好等于脚本路径。
pids_of() {
  TARGET="$1"
  for P in $(ps -A -o PID 2>/dev/null); do
    [ "$P" = "$$" ] && continue
    [ -d "/proc/$P" ] || continue
    ST=$(awk '{ print $3 }' "/proc/$P/stat" 2>/dev/null)
    case "$ST" in Z|X|x|"") continue ;; esac
    ARG2=$(tr '\000' '\n' < "/proc/$P/cmdline" 2>/dev/null | sed -n 2p)
    [ "$ARG2" = "$TARGET" ] && echo "$P"
  done
}

daemon_pids() {
  pids_of "$PL_TMP/plc-daemon.sh"
}

autopilot_pids() {
  pids_of "$PL_TMP/autopilot.sh"
}

stop_daemon() {
  for P in $(daemon_pids); do
    kill "$P" 2>/dev/null
  done
  rm -f "$PL_TMP/daemon.pid" 2>/dev/null
  sleep 1
  # 仍在跑就强杀，避免"以为停了其实还在采"
  for P in $(daemon_pids); do
    kill -9 "$P" 2>/dev/null
  done
}

start_daemon() {
  [ -x "$DAEMON" ] || { say "缺 $DAEMON"; return 1; }
  # 防止重复启动
  if [ -n "$(daemon_pids)" ]; then
    return 0
  fi
  CI=${1:-$CPU_INT}
  DI=${2:-$DIM_INT}
  setsid sh "$DAEMON" "$CI" "$DI" >/dev/null 2>&1 &
  sleep 2
}

read_screen() {
  # 返回 on / off / unknown
  S=$(dumpsys power 2>/dev/null | grep -m1 'mWakefulness=')
  case "$S" in
    *Awake*) echo on ;;
    *Asleep*|*Dozing*) echo off ;;
    *) echo unknown ;;
  esac
}

case "$1" in
  on)
    rm -f "$LOCK"
    echo "on" > "$CONF"
    start_daemon
    write_state on "$( [ -n "$(daemon_pids)" ] && echo 1 || echo 0 )" "手动开启"
    say "已开启"
    ;;
  off)
    echo "off" > "$CONF"
    : > "$LOCK"
    # 兼容清理：老版本可能还留着 autopilot 进程
    for P in $(autopilot_pids); do
      kill "$P" 2>/dev/null
    done
    stop_daemon
    write_state off 0 "手动关闭"
    say "已关闭（守护与自动调度均已退出，无采集开销）"
    ;;
  auto)
    # ===== auto = 「息屏增采」，不是「息屏暂停」=====
    #
    # 早期把 auto 设计成"熄屏 10 分钟停采、亮屏恢复"，方向是错的：
    # 用户最关心的恰恰是夜里息屏那几小时谁在耗电，停采等于
    # 在最该录像的时候关机。
    #
    # 现在 auto 的含义反过来：
    #   亮屏 → 常规间隔（60s），用户在用，数据密度够看趋势
    #   熄屏 → 加密到 30s，因为息屏耗电是最需要细粒度的场景，
    #          漏掉一个 30 分钟的唤醒黑洞就白测了
    #
    # 开销对比：每 60s 一次 su + procfs ≈ 30ms，翻倍到 30s 也只是
    # 占总时间的 0.1%，换来的是息屏归因分辨率提高一倍。
    echo "auto" > "$CONF"
    rm -f "$LOCK"
    stop_daemon

    if [ "$(read_screen)" = "on" ]; then
      start_daemon 60 1200
      write_state auto "$( [ -n "$(daemon_pids)" ] && echo 1 || echo 0 )" "亮屏，常规间隔 60s"
    else
      start_daemon 30 1200
      write_state auto "$( [ -n "$(daemon_pids)" ] && echo 1 || echo 0 )" "息屏，加密间隔 30s"
    fi
    say "已切到自动模式（息屏增采，不停采）"
    ;;

  status)
    MODE=$(cat "$CONF" 2>/dev/null || echo unset)
    N=$(daemon_pids | wc -l | tr -d ' ')
    echo "mode=$MODE"
    echo "daemon_count=$N"
    echo "screen=$(read_screen)"
    [ -f "$STATE" ] && cat "$STATE"
    ;;
  *)
    echo "用法: $0 {on|off|auto|status}"
    exit 2
    ;;
esac