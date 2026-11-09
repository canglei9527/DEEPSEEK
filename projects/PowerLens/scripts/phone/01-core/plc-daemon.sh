#!/system/bin/sh
# plc-daemon.sh -- PowerLens 采集守护 v5（绝对时间调度）
#
# ===== v4 的问题 =====
#   用 tick 计数决定何时跑 dims：
#     MOD=$((CPU_TICK % DIM_EVERY))
#   但每轮实际耗时 = CPU_INT + 采集耗时 + su 开销，实测 71.8 秒而非 60 秒。
#   导致 20 * 71.8 = 24 分钟才跑一次 dims（设定是 20 分钟）。
#   而且守护重启后 tick 归零，dims 会推迟很久。
#
# ===== v5 做法：绝对时间调度 =====
#   记录「上次 dims 的 epoch 秒」，每轮比较当前时间：
#     if [ $((NOW - LAST_DIM)) -ge $DIM_INT ]; then run_dims; fi
#   这样无论每轮耗时多少，dims 都按真实时间间隔触发。
#   同时也修了重启后 tick 归零的问题。
#
# ===== 采集调用必须带 su（实测）=====
#   守护脚本本身是 root（su -c "sh daemon.sh" 启动），
#   但它 fork 出的子进程会掉回 shell 身份。
#   实测：直接调用 plcollect → 0 UID / charge=-1（读不到 /proc/uid_cputime）
#         su -c 调用      → 156 UID 正常
#   所以每条采集命令都必须显式 su -c，开销 26 ms/次，可接受。
#
# ===== 用法 =====
#   su -c "setsid sh /data/local/tmp/plc/plc-daemon.sh [CPU间隔] [维度间隔]"
#   默认 60 1200

CPU_INT=${1:-60}
DIM_INT=${2:-1200}

BIN=/data/local/tmp/plc/plcollect
DIMBIN=/data/local/tmp/plc/pldims
STORE=/data/local/tmp/powerlens/snapshots.tsv
DIMS=/data/local/tmp/powerlens/dims.tsv
LOG=/data/local/tmp/plc/daemon.log
PIDF=/data/local/tmp/plc/daemon.pid

[ -x "$BIN" ] || { echo "$(date '+%F %T') FATAL: 缺 $BIN" >> $LOG; exit 1; }

# ---- 单实例保护 ----
if [ -f "$PIDF" ]; then
  OLD=$(cat "$PIDF" 2>/dev/null)
  if [ -n "$OLD" ] && [ -d "/proc/$OLD" ]; then
    CMD=$(tr -d '\000' < "/proc/$OLD/cmdline" 2>/dev/null)
    case "$CMD" in
      *plc-daemon*) echo "$(date '+%F %T') 已有实例 pid=$OLD，退出" >> $LOG; exit 0 ;;
    esac
  fi
fi
echo $$ > "$PIDF"

mkdir -p /data/local/tmp/powerlens 2>/dev/null
chmod 777 /data/local/tmp/powerlens 2>/dev/null
chmod 666 "$STORE" "$DIMS" 2>/dev/null

echo "$(date '+%F %T') plc-daemon v5 start pid=$$ cpu=${CPU_INT}s dim=${DIM_INT}s" >> $LOG

# ---- 绝对时间调度 ----
NOW=$(date +%s)
LAST_DIM=$((NOW - DIM_INT))    # 第一轮就立即跑一次 dims（建基线）
N_CPU=0
N_DIM=0
ALIVE_AT=$NOW

while true; do
  NOW=$(date +%s)

  # ---- 高频：CPU / IO ----
  su -c "$BIN --once -f $STORE -q" >/dev/null 2>&1
  chmod 666 "$STORE" 2>/dev/null
  N_CPU=$((N_CPU + 1))

  # ---- 低频：维度（按绝对时间判断）----
  if [ $((NOW - LAST_DIM)) -ge "$DIM_INT" ] && [ -x "$DIMBIN" ]; then
    OUT=$(su -c "$DIMBIN -f $DIMS" 2>&1)
    case "$OUT" in
      *增量*|*基线*) echo "$(date '+%F %T') dims ok: $OUT" >> $LOG ;;
      *)             echo "$(date '+%F %T') dims FAIL: $OUT" >> $LOG ;;
    esac
    chmod 666 "$DIMS" 2>/dev/null
    LAST_DIM=$NOW
    N_DIM=$((N_DIM + 1))
  fi

  # ---- 心跳（每 30 分钟一次）----
  if [ $((NOW - ALIVE_AT)) -ge 1800 ]; then
    echo "$(date '+%F %T') alive cpu_n=$N_CPU dim_n=$N_DIM snap=$(wc -l < $STORE 2>/dev/null)L dims=$(wc -l < $DIMS 2>/dev/null)L next_dim=$(( DIM_INT - (NOW - LAST_DIM) ))s" >> $LOG
    ALIVE_AT=$NOW
  fi

  # ---- 睡眠：对齐到下一个整周期，避免累积漂移 ----
  NOW2=$(date +%s)
  ELAPSED=$((NOW2 - NOW))
  SLEEP=$((CPU_INT - ELAPSED))
  [ "$SLEEP" -lt 1 ] && SLEEP=1
  sleep "$SLEEP"
done