#!/system/bin/sh
# service.sh -- late_start service，模块的主要启动入口
#
# 相比 service.d 的优势：
#   * 模块被禁用/卸载时自动不执行，不会留下孤儿进程
#   * 由模块管理器统一管理生命周期
#
# 采集间隔可在下面两个变量调整；也可用 /data/local/tmp/plc/set-interval.sh 修改。

MODDIR=${0%/*}

PL_TMP=/data/local/tmp/plc
PL_DATA=/data/local/tmp/powerlens
DAEMON=$PL_TMP/plc-daemon.sh
LOG=$PL_TMP/daemon.log
PINGLOOP=/data/local/tmp/wifi-ping-loop.sh

CPU_INTERVAL=60
DIM_INTERVAL=1200

# 允许用户覆盖间隔：文件里写 "60 1200" 形式
CFG=$PL_TMP/interval.conf
if [ -f "$CFG" ]; then
  read -r A B < "$CFG" 2>/dev/null
  [ -n "$A" ] && CPU_INTERVAL=$A
  [ -n "$B" ] && DIM_INTERVAL=$B
fi

mkdir -p "$PL_DATA" 2>/dev/null
chmod 777 "$PL_DATA" 2>/dev/null
touch "$PL_DATA/snapshots.tsv" "$PL_DATA/dims.tsv" 2>/dev/null
chmod 666 "$PL_DATA/snapshots.tsv" "$PL_DATA/dims.tsv" 2>/dev/null

if [ ! -x "$PL_TMP/plcollect" ]; then
  echo "$(date '+%F %T') module: missing plcollect, skip" >> "$LOG"
  exit 0
fi

# ---- Wi-Fi 保活（可选）----
# 息屏后射频被挂起会导致无线调试失联；这个循环让网络栈保持活跃。
# 不装 pingloop 也不影响采集。
if [ -f "$PINGLOOP" ]; then
  for P in $(ps -A -o PID,ARGS 2>/dev/null | grep 'wifi-ping-loop' | grep -v grep | awk '{print $1}'); do
    kill -9 "$P" 2>/dev/null
  done
  rm -f /data/local/tmp/keep-wifi.pid
  settings put global wifi_sleep_policy 2 2>/dev/null
  setsid sh "$PINGLOOP" >/dev/null 2>&1 &
  echo "$(date '+%F %T') module: wifi-ping-loop started pid=$!" >> "$LOG"
fi

# ---- 停掉可能残留的旧守护（模块重装/升级场景）----
for P in $(ps -A -o PID,ARGS 2>/dev/null | grep 'plc-daemon.sh' | grep -v grep | awk '{print $1}'); do
  kill "$P" 2>/dev/null
done
rm -f "$PL_TMP/daemon.pid" 2>/dev/null

# ---- 重放用户上次选择的 logd 开关 ----
# 只在用户显式开过（collect 无关，独立配置）时才动作，默认不动系统服务。
LOGD_CTL=$PL_TMP/logd-control.sh
if [ -x "$LOGD_CTL" ]; then
  sh "$LOGD_CTL" auto >/dev/null 2>&1
fi

# ---- 采集开关：尊重用户上次选择 ----
# collect.conf 缺失时按 on 处理（首次安装默认正常采集）。
COLLECT_CONF=$PL_TMP/collect.conf
COLLECT_MODE=on
[ -f "$COLLECT_CONF" ] && COLLECT_MODE=$(cat "$COLLECT_CONF" 2>/dev/null)
[ -z "$COLLECT_MODE" ] && COLLECT_MODE=on

if [ "$COLLECT_MODE" = "off" ]; then
  # 用户主动关闭：不启守护，做到真正零后台
  : > "$PL_TMP/collect.lock"
  {
    echo "mode=off"
    echo "running=0"
    echo "since=$(date +%s)"
    echo "reason=开机时保持关闭状态"
  } > "$PL_TMP/collect.state" 2>/dev/null
  chmod 666 "$PL_TMP/collect.state" 2>/dev/null
  echo "$(date '+%F %T') module: collect=off, skip daemon" >> "$LOG"
  exit 0
fi

rm -f "$PL_TMP/collect.lock"

# ===== 为什么没有"息屏自动暂停" =====
#   曾经做过 auto 模式：熄屏 10 分钟停采集、亮屏恢复。
#   方向是错的，已删除。理由：
#
#     这个 App 的核心问题就是「息屏时谁在偷偷耗电」。
#     熄屏恰好是最该记录的时段，停采等于在最该录像时关机，
#     而用户第二天想看的正是夜里那几小时。
#
#   而且关闭采集并不能省下多少电：实测守护每 60 秒一次 su +
#   procfs 读取，CPU 时间可忽略；真正耗电的是被观测的那些 App。
#   为了省这点开销丢掉整夜数据，明显不划算。
#
#   要停就显式停（collect=off），不要用"看不见的自动"。
setsid sh "$DAEMON" "$CPU_INTERVAL" "$DIM_INTERVAL" >/dev/null 2>&1 &
echo "$(date '+%F %T') module: plc-daemon started pid=$! cpu=${CPU_INTERVAL}s dim=${DIM_INTERVAL}s" >> "$LOG"