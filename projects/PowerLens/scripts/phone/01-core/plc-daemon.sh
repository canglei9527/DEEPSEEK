#!/system/bin/sh
# plc-daemon.sh -- PowerLens 采集守护 v4（双采集器）
#
# ===== 架构 =====
#   plcollect（高频，60s）→ procfs → CPU / IO         → snapshots.tsv
#   pldims   （低频，600s）→ dumpsys → fg/bg/fgs/wl   → dims.tsv
#
# ===== 为什么两个采集器 =====
#   procfs 快（97ms）但没有「前台/后台/前台服务/唤醒锁」维度；
#   dumpsys 慢（666ms）但有全部维度。
#   所以：CPU 用 procfs 高频刷，维度用 dumpsys 低频补。
#   App 侧按 dims 的占比把 CPU 分配到各维度（见 Dims.java）。
#
# ===== KernelSU su 边界（踩过很多次，务必遵守）=====
#   ① su -c "cmd"               → cmd 是 root ✓
#   ② su -c "cmd1; cmd2"        → 只有 cmd1 是 root ✗
#   ③ su -c "sh script"         → script 是 root，但内部 cp/awk/ls 会掉回 shell ✗
#   ④ echo x | su -c tee file   → tee 是 root ✓（写 sysfs 必须这样）
#   ⑤ su -c "echo x > file"     → 重定向由普通 shell 执行 ✗
#
#   本脚本用 ① 的形式，每条命令单独 su -c，且不用重定向。
#
# ===== 用法 =====
#   su -c "setsid sh /data/local/tmp/plc/plc-daemon.sh [CPU间隔] [维度间隔]"
#   默认 60 1200（CPU 每分钟，维度每 20 分钟）

CPU_INT=${1:-60}
DIM_INT=${2:-1200}

BIN=/data/local/tmp/plc/plcollect
DIMBIN=/data/local/tmp/plc/pldims
STORE=/data/local/tmp/powerlens/snapshots.tsv
DIMS=/data/local/tmp/powerlens/dims.tsv
LOG=/data/local/tmp/plc/daemon.log
PIDF=/data/local/tmp/plc/daemon.pid

[ -x "$BIN" ] || { echo "FATAL: 缺 $BIN" >> $LOG; exit 1; }

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

echo "$(date '+%F %T') plc-daemon v4 start pid=$$ cpu=${CPU_INT}s dim=${DIM_INT}s" >> $LOG

CPU_TICK=0
DIM_EVERY=$((DIM_INT / CPU_INT))
[ "$DIM_EVERY" -lt 1 ] && DIM_EVERY=1

while true; do
  # ---- 高频：CPU / IO ----
  su -c "$BIN --once -f $STORE -q" >/dev/null 2>&1
  chmod 666 "$STORE" 2>/dev/null

  # ---- 低频：维度 ----
  MOD=$((CPU_TICK % DIM_EVERY))
  if [ "$MOD" -eq 0 ] && [ -x "$DIMBIN" ]; then
    OUT=$(su -c "$DIMBIN -f $DIMS" 2>&1)
    case "$OUT" in
      *解析*) echo "$(date '+%F %T') dims ok: $OUT" >> $LOG ;;
      *)      echo "$(date '+%F %T') dims FAIL: $OUT" >> $LOG ;;
    esac
    chmod 666 "$DIMS" 2>/dev/null
  fi

  CPU_TICK=$((CPU_TICK + 1))

  # 心跳（每小时一次）
  N=$(( $(date +%s) / CPU_INT % 60 ))
  [ "$N" -eq 0 ] && echo "$(date '+%F %T') alive tick=$CPU_TICK cpu=$(wc -c < $STORE 2>/dev/null)B dims=$(wc -l < $DIMS 2>/dev/null)L" >> $LOG

  sleep "$CPU_INT"
done