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
  # ===== 为什么不用 /proc/<pid>/cmdline 逐字节比对 =====
  #   上一版把 cmdline 的 NUL 换成换行后取第 2 行，再与目标路径比较。
  #   真机实测：ps 里明明有 `sh /data/local/tmp/plc/plc-daemon.sh 60 1200`，
  #   但这个循环返回 NO MATCH，于是 `off` 杀不掉守护 ——
  #   用户点了「关闭采集」数据还在涨，比"没有开关"更糟。
  #   原因是 tr/sed 在 Android toybox 下对 NUL 的处理不稳定，
  #   不同 ROM 行为不一致，不能作为判定依据。
  #
  # ===== 现在的做法 =====
  #   用 ps 的 PID,STAT,ARGS 三列，要求
  #     第 3 列 == "sh" 且 第 4 列 == 目标脚本路径
  #   锚定在开头，所以 `su -c "sh ..."` 这类包装层（ARGS 以 su / sh -c
  #   开头）不会误命中；STAT 以 Z 开头的是 zombie，跳过。
  ps -A -o PID,STAT,ARGS 2>/dev/null \
    | awk -v me=$$ -v t="$1" '
        $1 == me        { next }
        $2 ~ /^Z/       { next }
        $3 == "sh" && $4 == t { print $1 }
      '
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
    # ===== 一致性守卫：用户意图优先 =====
    #   实测出现过 conf=off 但守护仍在跑：守护是模块安装前
    #   由 on/auto 拉起的，而 conf 后来被写成 off（或安装时保留了
    #   旧的 off 选择）。此时界面上"已关闭"和"daemon_count=1"互相
    #   矛盾，用户不知道信哪个。
    #   规则很简单：显式 off 就意味着不该有守护，发现残留就收掉。
    if [ "$MODE" = "off" ]; then
      _STRAY=$(daemon_pids)
      if [ -n "$_STRAY" ]; then
        say "conf=off 但守护在跑($_STRAY)，收掉"
        stop_daemon
      fi
      : > "$LOCK" 2>/dev/null
    fi
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